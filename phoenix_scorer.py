#!/usr/bin/env python3
"""Phoenix engagement scorer for the elxecutor autopilot.

Wraps the X Phoenix ranker model (x-algorithm/phoenix) to predict engagement
probabilities (fav, reply, retweet, dwell) for candidate tweets given the
account's own history. Used by reply_engine and quote_engine as a
model-based ranking signal on top of the Groq LLM scoring.

The model is loaded lazily once per process (it needs ~1.5GB for the ranker
embedding tables) and reused across cycles.
"""

import json
import logging
import os

import numpy as np

log = logging.getLogger(__name__)

ARTIFACTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "x-algorithm", "phoenix", "artifacts", "oss-phoenix-artifacts",
)

IDX_FAV = 1
IDX_REPLY = 4
IDX_RT = 6
IDX_DWELL = 11
IDX_VQV = 13

_WEIGHTS = {IDX_FAV: 1.0, IDX_REPLY: 0.5, IDX_RT: 0.3, IDX_DWELL: 0.2}

_singleton = None


def get_scorer():
    """Return a lazily-created singleton PhoenixScorer."""
    global _singleton
    if _singleton is None:
        _singleton = PhoenixScorer(ARTIFACTS_DIR)
    return _singleton


def build_history(client, state):
    """Build Phoenix history from the account's likes + past replies.

    Returns a list of {post_id, author_id, actions} where actions maps the
    action index (int/str) to a value (float): 1=fav, 4=reply."""
    history = []
    try:
        likes, _ = client.get_likes(count=40)
        for t in likes:
            history.append({
                "post_id": t["id_str"],
                "author_id": t["author_id"],
                "actions": {"1": 1.0},
            })
    except Exception as e:
        log.warning("Could not fetch likes for history: %s", e)

    # Past replies: resolve each replied tweet's author from the timeline so the
    # ranker sees who the account already engages with.
    tweet_ids = [str(tid) for tid in state.get("replied", {}).keys()]
    if tweet_ids:
        try:
            tweets, _ = client.get_timeline(count=100)
            by_id = {t["id_str"]: t for t in tweets}
            for tid in tweet_ids:
                t = by_id.get(tid)
                if t:
                    history.append({
                        "post_id": t["id_str"],
                        "author_id": t["author_id"],
                        "actions": {"1": 1.0, "4": 1.0},
                    })
        except Exception as e:
            log.warning("Could not fetch timeline for reply history: %s", e)
    return history


def rank_candidates(client, state, candidates):
    """Score candidates with Phoenix and return them sorted by predicted weighted engagement.

    On failure (model unavailable, network, etc.) returns candidates unchanged so the
    caller can fall back to a raw engagement heuristic."""
    try:
        me = client.get_me()
        history = build_history(client, state)
        cands = [{"post_id": t["id_str"], "author_id": t["author_id"]} for t in candidates]
        scores = get_scorer().score_tweets(me["id_str"], history, cands)
        by_id = {s["post_id"]: s for s in scores}
        for t in candidates:
            s = by_id.get(t["id_str"])
            t["phoenix"] = s or {"weighted": 0.0, "fav": 0.0, "reply": 0.0, "dwell": 0.0}
        return sorted(candidates, key=lambda t: -t["phoenix"]["weighted"])
    except Exception as e:
        log.warning("Phoenix scoring unavailable (%s); falling back to engagement heuristic.", e)
        return candidates


class PhoenixScorer:
    """Loads the Phoenix ranker artifacts and scores candidate (post_id, author_id) pairs."""

    def __init__(self, artifacts_dir: str):
        import jax
        import jax.numpy as jnp
        import haiku as hk

        self.jax = jax

        from grok import TransformerConfig
        from recsys_model import HashConfig, PhoenixModelConfig, RecsysBatch, RecsysEmbeddings
        from runners import load_embedding_table, load_model_params

        self.RecsysBatch = RecsysBatch
        self.RecsysEmbeddings = RecsysEmbeddings

        self.jnp = jnp

        with open(os.path.join(artifacts_dir, "ranker", "config.json")) as f:
            cfg = json.load(f)

        self.cfg = cfg
        self.emb_size = cfg["emb_size"]
        self.num_actions = cfg["num_actions"]
        self.hist_len = cfg["history_seq_len"]
        self.cand_len = cfg["candidate_seq_len"]

        log.info("Loading Phoenix ranker params...")
        params = load_model_params(os.path.join(artifacts_dir, "ranker", "model_params.npz"))
        self.params = params

        log.info("Loading Phoenix ranker embedding tables (%.1f GB)...",
                 os.path.getsize(os.path.join(artifacts_dir, "ranker", "embedding_tables.npz")) / 1e9)
        emb_dict = load_embedding_table(
            os.path.join(artifacts_dir, "ranker", "embedding_tables.npz"))
        self.emb = self._build_unified_table(emb_dict, cfg)

        self.hash_user, self.hash_item, self.hash_author = self._build_hash_functions(cfg)

        model_config = PhoenixModelConfig(
            emb_size=cfg["emb_size"],
            history_seq_len=cfg["history_seq_len"],
            candidate_seq_len=cfg["candidate_seq_len"],
            hash_config=HashConfig(
                num_user_hashes=cfg["num_user_hashes"],
                num_item_hashes=cfg["num_item_hashes"],
                num_author_hashes=cfg["num_author_hashes"],
            ),
            product_surface_vocab_size=cfg.get("product_surface_vocab_size", 16),
            num_actions=cfg["num_actions"],
            model=TransformerConfig(
                emb_size=cfg["emb_size"],
                key_size=cfg["key_size"],
                num_q_heads=cfg["num_heads"],
                num_kv_heads=cfg["num_heads"],
                num_layers=cfg["num_layers"],
                widening_factor=2.0,
                attn_output_multiplier=0.125,
            ),
        )
        model_config.initialize()

        def forward(batch, embeddings):
            return model_config.make()(batch, embeddings)
        self.rank_fn = hk.without_apply_rng(hk.transform(forward))
        log.info("Phoenix ranker loaded.")

    @staticmethod
    def _build_unified_table(emb_dict, cfg):
        emb_size = cfg["emb_size"]
        uv = cfg["user_vocab_size"]
        iv = cfg["item_vocab_size"]
        av = cfg["author_vocab_size"]
        pad = 65
        table = np.zeros((pad + uv + iv + av, emb_size), dtype=np.float32)
        table[pad: pad + uv] = emb_dict["user_embeddings"]
        table[pad + uv: pad + uv + iv] = emb_dict["item_embeddings"]
        table[pad + uv + iv: pad + uv + iv + av] = emb_dict["author_embeddings"]
        return table

    @staticmethod
    def _hash_ids(ids, scales, biases, modulus, num_buckets):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ids = np.asarray(ids, dtype=np.int64).ravel()
            scales = np.array(scales, dtype=np.int64)
            biases = np.array(biases, dtype=np.int64)
            n, m = len(ids), len(scales)
            out = np.empty((n, m), dtype=np.int32)
            for i in range(n):
                for j in range(m):
                    raw = (ids[i] * scales[j] + biases[j]) % np.int64(modulus)
                    out[i, j] = 0 if ids[i] == 0 else int((int(raw) % (num_buckets - 1)) + 1)
        return out

    def _build_hash_functions(self, config):
        hp = config["hash_params"]
        pad = 65
        uv = config["user_vocab_size"]
        iv = config["item_vocab_size"]
        av = config["author_vocab_size"]

        def hash_user(user_ids):
            h = self._hash_ids(user_ids, hp["user_hash_scales"], hp["user_biases"],
                               hp["user_modulus"], uv)
            return np.where(h == 0, 0, h + pad)

        def hash_item(item_ids):
            h = self._hash_ids(item_ids, hp["item_hash_scales"], hp["item_biases"],
                               hp["item_modulus"], iv)
            return np.where(h == 0, 0, h + pad + uv)

        def hash_author(author_ids):
            h = self._hash_ids(author_ids, hp["author_hash_scales"], hp["author_biases"],
                               hp["author_modulus"], av)
            return np.where(h == 0, 0, h + pad + uv + iv)

        return hash_user, hash_item, hash_author

    def _make_batch(self, user_id, history_post_ids, history_author_ids, history_actions,
                    cand_post_ids, cand_author_ids):
        jnp = self.jnp
        B = 1
        hist_len = self.hist_len
        cand_len = self.cand_len
        n_cand = len(cand_post_ids)

        user_hashes = self.hash_user(np.array([user_id], dtype=np.uint64))

        hist_post = self.hash_item(history_post_ids).reshape(B, hist_len, -1)
        hist_auth = self.hash_author(history_author_ids).reshape(B, hist_len, -1)

        if n_cand < cand_len:
            pad_n = cand_len - n_cand
            cph = np.pad(self.hash_item(cand_post_ids).reshape(1, n_cand, -1),
                         ((0, 0), (0, pad_n), (0, 0)))
            cah = np.pad(self.hash_author(cand_author_ids).reshape(1, n_cand, -1),
                         ((0, 0), (0, pad_n), (0, 0)))
        else:
            cph = self.hash_item(cand_post_ids[:cand_len]).reshape(1, cand_len, -1)
            cah = self.hash_author(cand_author_ids[:cand_len]).reshape(1, cand_len, -1)

        batch = self.RecsysBatch(
            user_hashes=jnp.asarray(user_hashes),
            history_post_hashes=jnp.asarray(hist_post),
            history_author_hashes=jnp.asarray(hist_auth),
            history_actions=jnp.asarray(history_actions.reshape(B, hist_len, self.num_actions)),
            history_product_surface=jnp.zeros((B, hist_len), dtype=jnp.int32),
            candidate_post_hashes=jnp.asarray(cph),
            candidate_author_hashes=jnp.asarray(cah),
            candidate_product_surface=jnp.zeros((B, cand_len), dtype=jnp.int32),
        )
        return batch, n_cand

    def _make_embeddings(self, user_id, history_post_ids, history_author_ids,
                         cand_post_ids, cand_author_ids, n_cand):
        jnp = self.jnp
        B = 1
        hist_len = self.hist_len
        cand_len = self.cand_len
        emb = self.emb

        user_hashes = self.hash_user(np.array([user_id], dtype=np.uint64))
        hist_post = self.hash_item(history_post_ids).reshape(B, hist_len, -1)
        hist_auth = self.hash_author(history_author_ids).reshape(B, hist_len, -1)

        if n_cand < cand_len:
            pad_n = cand_len - n_cand
            cph = np.pad(self.hash_item(cand_post_ids).reshape(1, n_cand, -1),
                         ((0, 0), (0, pad_n), (0, 0)))
            cah = np.pad(self.hash_author(cand_author_ids).reshape(1, n_cand, -1),
                         ((0, 0), (0, pad_n), (0, 0)))
        else:
            cph = self.hash_item(cand_post_ids[:cand_len]).reshape(1, cand_len, -1)
            cah = self.hash_author(cand_author_ids[:cand_len]).reshape(1, cand_len, -1)

        return self.RecsysEmbeddings(
            user_embeddings=jnp.asarray(emb[user_hashes]),
            history_post_embeddings=jnp.asarray(emb[hist_post]),
            history_author_embeddings=jnp.asarray(emb[hist_auth]),
            candidate_post_embeddings=jnp.asarray(emb[cph]),
            candidate_author_embeddings=jnp.asarray(emb[cah]),
        )

    def score_tweets(self, user_id: int, history: list[dict], candidates: list[dict]) -> list[dict]:
        """Score candidate tweets with the Phoenix ranker.

        Args:
            user_id: the account's own user id (int).
            history: list of {post_id, author_id, actions} where actions maps action-index
                     (int/str) -> value (float). post_id/author_id may be str or int.
            candidates: list of {post_id, author_id}.

        Returns:
            List (aligned to candidates) of dicts with predicted probabilities
            {post_id, fav, reply, rt, dwell, vqv, weighted}.
        """
        hist_len = self.hist_len
        num_actions = self.num_actions

        history_post_ids = np.zeros(hist_len, dtype=np.uint64)
        history_author_ids = np.zeros(hist_len, dtype=np.uint64)
        history_actions = np.zeros((hist_len, num_actions), dtype=np.float32)

        for i, item in enumerate(history[-hist_len:]):
            history_post_ids[i] = int(item["post_id"])
            history_author_ids[i] = int(item["author_id"])
            for act_idx_str, act_val in item.get("actions", {}).items():
                idx = int(act_idx_str)
                if 0 <= idx < num_actions:
                    history_actions[i, idx] = float(act_val)

        cand_post_ids = np.array([int(c["post_id"]) for c in candidates], dtype=np.uint64)
        cand_author_ids = np.array([int(c["author_id"]) for c in candidates], dtype=np.uint64)

        if len(cand_post_ids) == 0:
            return []

        cand_len = self.cand_len
        results = []
        for start in range(0, len(candidates), cand_len):
            chunk = candidates[start:start + cand_len]
            cposts = cand_post_ids[start:start + cand_len]
            cauths = cand_author_ids[start:start + cand_len]

            batch, n_cand = self._make_batch(
                int(user_id), history_post_ids, history_author_ids, history_actions,
                cposts, cauths)
            embeddings = self._make_embeddings(
                int(user_id), history_post_ids, history_author_ids,
                cposts, cauths, n_cand)

            try:
                out = self.rank_fn.apply(self.params, batch, embeddings)
                probs = np.asarray(self.jax.nn.sigmoid(out.logits)[0, :n_cand, :])
            except Exception as e:
                log.error("Phoenix ranker failed: %s", e)
                raise

            for i, cand in enumerate(chunk):
                p = probs[i]
                weighted = sum(_WEIGHTS[idx] * float(p[idx]) for idx in _WEIGHTS)
                results.append({
                    "post_id": cand["post_id"],
                    "fav": float(p[IDX_FAV]),
                    "reply": float(p[IDX_REPLY]),
                    "rt": float(p[IDX_RT]),
                    "dwell": float(p[IDX_DWELL]),
                    "vqv": float(p[IDX_VQV]),
                    "weighted": weighted,
                })
        return results
