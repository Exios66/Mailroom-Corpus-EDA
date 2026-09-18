#!/usr/bin/env python3
"""Fine-tune the hierarchical ModernBERT-base classifier.

Consumes the published ``Lucius-Morningstar/mailroom-modernbert-training``
dataset (or a local stage dir) and trains the ingest fast-path classifier:

- one shared ModernBERT-base backbone + 6 heads: ``doc_type`` (5 classes +
  ``unknown``) and one subclass head per doc_type (fires only on its class's
  rows — the pipeline's existing conditional structure),
- class-weighted cross-entropy per head (weights from ``labels.json``,
  inverse-frequency over the train split),
- AdamW bf16, LR 2e-5, linear schedule with 6% warmup, early stopping on
  validation loss,
- temperature scaling per head on the validation split (Platt-style),
- metrics: per-head window accuracy, document-level plurality-vote accuracy
  (the sorter's merge), macro-F1, ECE.

Usage:
    .venv/bin/python modernbert/train.py --epochs 5 --output data/modernbert_training/run1
    .venv/bin/python modernbert/train.py --push-to-hub Lucius-Morningstar/mailroom-modernbert-classifier
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# modernbert/ scripts sit outside scripts/, so the shared _bootstrap.py
# preamble does not apply — anchor on the repo root (.git), or on
# MODERNBERT_ROOT when running inside the Modal container (no .git there).
_b = Path(__file__).resolve()
if os.environ.get("MODERNBERT_ROOT"):
    ROOT = Path(os.environ["MODERNBERT_ROOT"])
else:
    while not (_b / ".git").is_dir():
        _b = _b.parent
    ROOT = _b
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "modernbert"))

from prep import DOC_TYPES, MAX_TOKENS, MODEL_ID, window_document  # noqa: E402

DEFAULT_DATA = "Lucius-Morningstar/mailroom-modernbert-training"
DEFAULT_OUTPUT = ROOT / "data" / "modernbert_training" / "runs" / "latest"


class HierarchicalClassifier(nn.Module):
    """Shared ModernBERT backbone + per-head linear classifiers."""

    def __init__(self, base_model, head_sizes: dict[str, int]):
        super().__init__()
        self.backbone = base_model
        self.heads = nn.ModuleDict(
            {name: nn.Linear(base_model.config.hidden_size, n, bias=True)
             for name, n in sorted(head_sizes.items())})

    def forward(self, input_ids, attention_mask):
        out = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
        # ModernBERT has no pooler — use the <s> first-token embedding
        # (RoBERTa-style; the model card's classification recipe). Cast to
        # float32: the backbone runs bf16, the heads are fp32.
        pooled = out.last_hidden_state[:, 0].float()
        return {name: head(pooled) for name, head in self.heads.items()}


def load_dataset(data: str, split: str) -> list[dict]:
    """Rows from the windows config (train/validation) or documents (test)."""
    import pandas as pd
    local = Path(data)
    if local.exists():
        cfg = "windows" if split != "test" else "documents"
        files = sorted((local / "parquet" / cfg / split).glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"no {cfg}/{split} parquet under {local}")
        return pd.read_parquet(files[0]).to_dict("records")
    # Remote Hub repo: the datasets-server exposes only an auto-converted
    # `default` config (union of windows+documents), so resolve the published
    # parquet/<cfg>/<split> folders directly via hf:// data_files globs.
    from datasets import load_dataset
    cfg = "windows" if split != "test" else "documents"
    glob = f"hf://datasets/{data}/parquet/{cfg}/{split}/*.parquet"
    ds = load_dataset("parquet", split=split, data_files={split: glob})
    return [dict(r) for r in ds]


def tokenize_rows(rows: list[dict], tokenizer, max_length: int) -> list[dict]:
    """Tokenize window text; rows carry doc_type/subclass labels."""
    enc = tokenizer([r["text"] for r in rows], padding="max_length",
                    truncation=True, max_length=max_length, return_tensors="pt")
    return [{
        "input_ids": enc["input_ids"][i],
        "attention_mask": enc["attention_mask"][i],
        "doc_type": r["doc_type"],
        "subclass": r["subclass"],
        "filename": r["filename"],
    } for i, r in enumerate(rows)]


def class_weight_tensor(weights: dict[str, float], labels: list[str],
                        device) -> torch.Tensor:
    return torch.tensor([weights.get(l, 1.0) for l in labels],
                        dtype=torch.float32, device=device)


def make_batches(rows, batch_size: int, shuffle: bool, heads, device):
    idx = list(range(len(rows)))
    if shuffle:
        random.shuffle(idx)
    for i in range(0, len(idx), batch_size):
        sel = [rows[j] for j in idx[i:i + batch_size]]
        yield {
            "input_ids": torch.stack([r["input_ids"] for r in sel]).to(device),
            "attention_mask": torch.stack([r["attention_mask"] for r in sel]).to(device),
            "doc_type": torch.tensor([heads["doc_type"]["label2id"][r["doc_type"]]
                                      for r in sel], device=device),
            "subclass": torch.tensor([heads[r["doc_type"]]["label2id"][r["subclass"]]
                                      for r in sel], device=device),
            "filename": [r["filename"] for r in sel],
        }


def head_loss(model, batch, heads, device) -> torch.Tensor:
    """doc_type CE on every row + subclass CE on each class's rows."""
    logits = model(batch["input_ids"], batch["attention_mask"])
    loss = F.cross_entropy(logits["doc_type"], batch["doc_type"],
                           weight=heads["doc_type"]["weight"])
    for cls in DOC_TYPES:
        sel = batch["doc_type"] == heads["doc_type"]["label2id"][cls]
        if sel.any():
            loss = loss + F.cross_entropy(
                logits[cls][sel], batch["subclass"][sel],
                weight=heads[cls]["weight"])
    return loss, logits


def train_epoch(model, batches, optimizer, scheduler, heads, device,
                grad_accum: int) -> float:
    model.train()
    total, n = 0.0, 0
    for step, batch in enumerate(batches):
        loss, _ = head_loss(model, batch, heads, device)
        (loss / grad_accum).backward()
        if (step + 1) % grad_accum == 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()
        total += loss.item()
        n += 1
    return total / max(1, n)


@torch.no_grad()
def evaluate(model, batches, heads, maps, device) -> dict:
    """Per-head window metrics + document-level plurality-vote metrics.

    The document vote mirrors the sorter's merge: doc_type by plurality over
    windows; subclass by plurality over windows whose doc_type vote is the
    winning class (the pipeline's conditional structure).
    """
    model.eval()
    logits_by_head: dict[str, list] = defaultdict(list)
    labels_by_head: dict[str, list] = defaultdict(list)
    doc_votes: dict[str, list] = defaultdict(list)  # fn -> [(dt_pred, sc_pred)]
    doc_labels: dict[str, tuple] = {}
    total_loss = 0.0
    n_batches = 0
    for batch in batches:
        loss, logits = head_loss(model, batch, heads, device)
        total_loss += loss.item()
        n_batches += 1
        dt_preds = logits["doc_type"].argmax(-1)
        sc_preds = {cls: logits[cls].argmax(-1) for cls in DOC_TYPES}
        for name, lg in logits.items():
            logits_by_head[name].append(lg.cpu())
            if name == "doc_type":
                labels_by_head[name].append(batch["doc_type"])
            else:
                # subclass heads are scored only on their own class's rows
                sel = batch["doc_type"] == heads["doc_type"]["label2id"][name]
                logits_by_head[name][-1] = lg[sel].cpu()
                labels_by_head[name].append(batch["subclass"][sel])
        for i, fn in enumerate(batch["filename"]):
            dt_p = dt_preds[i].item()
            cls = maps["doc_type"]["id2label"][str(dt_p)]
            doc_votes[fn].append((dt_p, sc_preds[cls][i].item()))
            doc_labels[fn] = (batch["doc_type"][i].item(),
                              batch["subclass"][i].item())
    metrics = {"val_loss": total_loss / max(1, n_batches)}
    for name in sorted(logits_by_head):
        lg = torch.cat(logits_by_head[name])
        lab = torch.cat(labels_by_head[name])
        metrics[f"{name}_window_acc"] = round(
            (lg.argmax(-1) == lab).float().mean().item(), 4)
        metrics[f"{name}_ece"] = round(ece(lg, lab), 4)
        metrics[f"{name}_macro_f1"] = round(macro_f1(lg, lab), 4)
    dt_correct = sc_correct = 0
    for fn, votes in doc_votes.items():
        dt_label, sc_label = doc_labels[fn]
        dt_pred = Counter(v[0] for v in votes).most_common(1)[0][0]
        if dt_pred == dt_label:
            dt_correct += 1
            cls = maps["doc_type"]["id2label"][str(dt_pred)]
            cond = [v[1] for v in votes if v[0] == dt_pred]
            sc_pred = Counter(cond).most_common(1)[0][0]
            if sc_pred == sc_label:  # both ids in head `cls`'s space
                sc_correct += 1
    metrics["doc_type_doc_acc"] = round(dt_correct / max(1, len(doc_votes)), 4)
    metrics["subclass_doc_acc"] = round(sc_correct / max(1, dt_correct), 4)
    return metrics


def ece(logits: torch.Tensor, labels: torch.Tensor, n_bins: int = 10) -> float:
    probs = F.softmax(logits, dim=-1)
    conf, pred = probs.max(-1)
    correct = (pred == labels).float()
    bins = torch.linspace(0, 1, n_bins + 1)
    total = 0.0
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        sel = (conf >= lo) & (conf < hi) if i < n_bins - 1 else (conf >= lo)
        if sel.sum() == 0:
            continue
        total += (sel.sum().item() / len(conf)) * abs(
            correct[sel].mean().item() - conf[sel].mean().item())
    return total


def macro_f1(logits: torch.Tensor, labels: torch.Tensor) -> float:
    preds = logits.argmax(-1)
    n_classes = logits.shape[1]
    f1s = []
    for c in range(n_classes):
        tp = ((preds == c) & (labels == c)).sum().item()
        fp = ((preds == c) & (labels != c)).sum().item()
        fn = ((preds != c) & (labels == c)).sum().item()
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return float(np.mean(f1s))


def fit_temperature(logits: torch.Tensor, labels: torch.Tensor) -> float:
    """Platt-style temperature scaling: T minimizing NLL on validation."""
    from scipy.optimize import minimize_scalar
    lg, lab = logits.detach().float().numpy(), labels.numpy()

    def nll(t: float) -> float:
        if t <= 1e-3:
            return 1e9
        z = lg / t
        z = z - z.max(axis=1, keepdims=True)
        log_probs = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        return -log_probs[np.arange(len(lab)), lab].mean()

    res = minimize_scalar(nll, bounds=(0.05, 10.0), method="bounded")
    return float(res.x)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=DEFAULT_DATA,
                    help="HF repo id or local stage dir")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--warmup-frac", type=float, default=0.06)
    ap.add_argument("--max-length", type=int, default=MAX_TOKENS)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--push-to-hub", default="",
                    help="model repo id to push the checkpoint to")
    ap.add_argument("--eval-test", action="store_true",
                    help="run the held-out test split through the trained "
                         "model (P0 gate)")
    ap.add_argument("--limit", type=int, default=0,
                    help="smoke-test: cap train/validation rows (0 = all)")
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}", flush=True)

    from transformers import AutoModel, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    # bf16 on CUDA only — CPU bf16 is emulated and pathologically slow
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    base = AutoModel.from_pretrained(MODEL_ID, torch_dtype=dtype)
    base = base.to(device)

    # head configs from the published labels.json
    local_data = Path(args.data)
    if local_data.exists():
        labels_path = local_data / "labels.json"
    else:
        from huggingface_hub import hf_hub_download
        labels_path = Path(hf_hub_download(args.data, "labels.json",
                                           repo_type="dataset"))
    maps = json.loads(labels_path.read_text())
    head_sizes = {name: len(cfg["labels"]) for name, cfg in maps.items()}
    model = HierarchicalClassifier(base, head_sizes).to(device)

    heads = {}
    for name, cfg in maps.items():
        heads[name] = {
            "label2id": cfg["label2id"],
            "weight": class_weight_tensor(cfg["weights"], cfg["labels"], device),
        }

    train_rows = tokenize_rows(load_dataset(args.data, "train"), tokenizer,
                               args.max_length)
    val_rows = tokenize_rows(load_dataset(args.data, "validation"), tokenizer,
                             args.max_length)
    if args.limit:
        train_rows = train_rows[:args.limit]
        val_rows = val_rows[:max(1, args.limit // 4)]
    print(f"windows: train {len(train_rows)} / validation {len(val_rows)}", flush=True)

    n_steps = math.ceil(len(train_rows) / args.batch_size)
    total_steps = n_steps * args.epochs
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    warmup = max(1, int(total_steps * args.warmup_frac))

    def lr_lambda(step: int) -> float:
        if step < warmup:
            return step / warmup
        return max(0.0, 1.0 - (step - warmup) / max(1, total_steps - warmup))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    best_val = float("inf")
    stale = 0
    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        loss = train_epoch(model, make_batches(train_rows, args.batch_size, True,
                                               heads, device),
                           optimizer, scheduler, heads, device, args.grad_accum)
        val = evaluate(model, make_batches(val_rows, args.batch_size, False,
                                           heads, device), heads, maps, device)
        print(f"epoch {epoch}/{args.epochs} loss {loss:.4f} "
              f"val_loss {val['val_loss']:.4f} "
              f"doc_type_acc {val['doc_type_window_acc']} "
              f"doc_acc {val['doc_type_doc_acc']} ece {val['doc_type_ece']}",
              flush=True)
        if val["val_loss"] < best_val:
            best_val = val["val_loss"]
            stale = 0
        else:
            stale += 1
            if stale >= 2:
                print(f"early stop at epoch {epoch}")
                break
    print(f"training wall: {time.time() - t0:.1f}s")

    # temperature scaling per head on validation logits
    temps: dict[str, float] = {}
    val_logits: dict[str, list] = defaultdict(list)
    val_labels: dict[str, list] = defaultdict(list)
    with torch.no_grad():
        for batch in make_batches(val_rows, args.batch_size, False, heads, device):
            lg = model(batch["input_ids"], batch["attention_mask"])
            for name, t in lg.items():
                if name == "doc_type":
                    val_logits[name].append(t.cpu())
                    val_labels[name].append(batch["doc_type"])
                else:
                    sel = batch["doc_type"] == heads["doc_type"]["label2id"][name]
                    val_logits[name].append(t[sel].cpu())
                    val_labels[name].append(batch["subclass"][sel])
    for name in val_logits:
        lg = torch.cat(val_logits[name])
        lab = torch.cat(val_labels[name])
        if len(lab) < 2 or len(set(lab.tolist())) < 2:
            temps[name] = 1.0  # too few rows to fit T — leave uncalibrated
        else:
            temps[name] = fit_temperature(lg, lab)
    print("temperatures:", {k: round(v, 3) for k, v in temps.items()})

    # save checkpoint
    args.output.mkdir(parents=True, exist_ok=True)
    model.backbone.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    torch.save({name: head.state_dict() for name, head in model.heads.items()},
               args.output / "heads.pt")
    (args.output / "labels.json").write_text(
        json.dumps(maps, sort_keys=True, indent=2))
    (args.output / "temperatures.json").write_text(
        json.dumps(temps, sort_keys=True, indent=2))
    print(f"checkpoint saved: {args.output}")

    if args.push_to_hub:
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(args.push_to_hub, repo_type="model", exist_ok=True)
        api.upload_folder(folder_path=str(args.output), repo_id=args.push_to_hub,
                          repo_type="model",
                          commit_message=f"ModernBERT hierarchical classifier "
                                         f"(epochs {args.epochs})")
        print(f"pushed: https://huggingface.co/{args.push_to_hub}")

    if args.eval_test:
        # held-out test: window each document at eval time (documents config
        # carries full text), plurality-vote the windows per head
        test_docs = load_dataset(args.data, "test")
        dt_correct = sc_correct = 0
        for r in test_docs:
            wins = window_document(r["title"], r["doc_text"])
            enc = tokenizer(wins, padding="max_length", truncation=True,
                            max_length=args.max_length, return_tensors="pt")
            with torch.no_grad():
                lg = model(enc["input_ids"].to(device),
                           enc["attention_mask"].to(device))
            dt_votes = Counter(lg["doc_type"].argmax(-1).tolist())
            dt_pred = dt_votes.most_common(1)[0][0]
            dt_label = heads["doc_type"]["label2id"][r["doc_type"]]
            if dt_pred == dt_label:
                dt_correct += 1
                cls = maps["doc_type"]["id2label"][str(dt_pred)]
                sc_votes = Counter(lg[cls].argmax(-1).tolist())
                sc_pred = sc_votes.most_common(1)[0][0]
                if sc_pred == heads[cls]["label2id"][r["subclass"]]:
                    sc_correct += 1
        n = len(test_docs)
        print(f"test doc_type acc: {dt_correct}/{n} = {dt_correct / n:.4f}")
        print(f"test subclass acc (conditional): {sc_correct}/{dt_correct} "
              f"= {sc_correct / max(1, dt_correct):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())