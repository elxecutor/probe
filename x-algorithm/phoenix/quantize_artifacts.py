#!/usr/bin/env python3
"""Quantize Phoenix embedding tables from fp32 to per-row int8.

Produces <name>_int8.npz alongside the original, with keys:
  user_embeddings  int8    (N, D)
  user_embeddings_scale float32 (N,)
  user_embeddings_zero  float32 (N,)
(and same for item_embeddings, author_embeddings)

Dequant: x_hat = (q + zero) * scale, per row.

Keeps peak RAM low by reading the source via mmap in row chunks and
writing the int8 output into preallocated int8 arrays.
"""

import os
import sys

import numpy as np


def quantize_rows(src, chunk=100_000):
    """Per-row asymmetric int8 quantization of an (N, D) mmap array.

    Yields nothing; returns (q_int8, scale, zero) arrays fully materialized.
    """
    n, d = src.shape
    q = np.empty((n, d), dtype=np.int8)
    scale = np.empty((n,), dtype=np.float32)
    zero = np.empty((n,), dtype=np.float32)
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block = np.asarray(src[start:end], dtype=np.float32)
        rmin = block.min(axis=1)
        rmax = block.max(axis=1)
        rng = rmax - rmin
        rng = np.where(rng < 1e-12, 1e-12, rng)
        s = rng / 127.0
        z = -rmin / s
        zr = np.clip(np.rint(z), -127.0, 127.0)
        q_block = np.clip(np.rint(block / s[:, None] + zr[:, None]), -127, 127)
        q[start:end] = q_block.astype(np.int8)
        scale[start:end] = s
        zero[start:end] = zr
    return q, scale, zero


def quantize_embedding_file(src_path, dst_path):
    src = np.load(src_path, mmap_mode="r")
    out = {}
    for name in ("user_embeddings", "item_embeddings", "author_embeddings"):
        q, s, z = quantize_rows(src[name])
        out[name] = q
        out[name + "_scale"] = s
        out[name + "_zero"] = z
        print(f"  {name}: {q.shape} {q.dtype} (scale min={s.min():.6f} max={s.max():.6f})")
    src.close()
    np.savez(dst_path, **out)
    print(f"  wrote {dst_path} ({os.path.getsize(dst_path)/1e6:.1f} MB)")


def main():
    artifacts_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    for sub in ("retrieval", "ranker"):
        src = os.path.join(artifacts_dir, sub, "embedding_tables.npz")
        dst = os.path.join(artifacts_dir, sub, "embedding_tables_int8.npz")
        if not os.path.exists(src):
            print(f"skip {sub}: {src} missing")
            continue
        print(f"quantizing {sub} ...")
        quantize_embedding_file(src, dst)


if __name__ == "__main__":
    main()
