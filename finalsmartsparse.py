# -*- coding: utf-8 -*-
"""FinalSmartSparse.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1_MeCBt3OxbipSXI1uMAwPuekBTIafweS
"""

# === Install Dependencies ===
!pip install transformers datasets accelerate -q

# === Imports ===
import torch
import torch.nn as nn
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import load_dataset
import numpy as np

# === Load Calibration Data from Raw File ===
import urllib.request

# === Device Setup ===
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Device:", device)

# === Load OPT-125M ===
model_name = "facebook/opt-125m"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name)
tokenizer.pad_token = tokenizer.eos_token
model.config.pad_token_id = tokenizer.eos_token_id
model.to(device)

# === SmartSparse Pruner ===
class SmartSparsePruner:
    def __init__(self, model, alpha=1/3, beta=1/3, gamma=1/3):
        self.model = model
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.device = next(model.parameters()).device
        self.layerwise_scores = {}

    def _normalize(self, score):
        mean = score.mean()
        std = score.std() + 1e-8
        return (score - mean) / std

    def _compute_pqi(self, weight, bits=8):
        qmin, qmax = weight.min(), weight.max()
        scale = (qmax - qmin) / (2**bits - 1)
        if scale < 1e-9:
            return torch.zeros_like(weight)
        q_weight = torch.round((weight - qmin) / scale) * scale + qmin
        return (weight - q_weight).pow(2)

    def _compute_movement(self, grad):
        return grad.abs()

    def _compute_hessian(self, weight, grad):
        h_diag = grad.pow(2)
        return (weight.pow(2) / (2 * h_diag.clamp(min=1e-6)))

    def compute_importance_scores(self, inputs):
        self.model.zero_grad()
        input_ids = inputs["input_ids"].to(self.device)
        labels = inputs["labels"].to(self.device)
        loss = self.model(input_ids=input_ids, labels=labels).loss
        loss.backward()

        for name, module in self.model.named_modules():
            if isinstance(module, nn.Linear) and module.weight.grad is not None:
                w = module.weight.data
                g = module.weight.grad

                pqi = self._normalize(self._compute_pqi(w))
                move = self._normalize(self._compute_movement(g))
                hess = self._normalize(self._compute_hessian(w, g))
                fused = self.alpha * pqi + self.beta * move + self.gamma * hess

                self.layerwise_scores[name] = fused

    def prune(self, sparsity=0.5):
        all_scores = []
        module_map = {}
        for name, module in self.model.named_modules():
            if isinstance(module, nn.Linear) and name in self.layerwise_scores:
                scores = self.layerwise_scores[name].flatten()
                all_scores.append(scores)
                module_map[name] = module
        all_scores = torch.cat(all_scores)
        k = int((1 - sparsity) * all_scores.numel())
        threshold = torch.topk(all_scores, k, largest=True).values[-1]

        for name, module in module_map.items():
            mask = (self.layerwise_scores[name] >= threshold).float()
            module.weight.data.mul_(mask)

# === Load Calibration Data Safely ===
def load_wikitext2(num_samples=300):
    import urllib.request

    url = "https://raw.githubusercontent.com/pytorch/examples/master/word_language_model/data/wikitext-2/train.txt"
    file_path = "/tmp/wikitext-2-train.txt"
    urllib.request.urlretrieve(url, file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith('=')]

    # Group lines into ~paragraphs
    paragraphs = []
    buffer = ""
    for line in lines:
        buffer += line + " "
        if len(buffer.split()) > 100:
            paragraphs.append(buffer.strip())
            buffer = ""
        if len(paragraphs) >= num_samples:
            break

    return paragraphs


# === Encode and Prepare Inputs ===
def prepare_inputs(texts, tokenizer, max_len=64):
    enc = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len)
    input_ids = enc["input_ids"]
    labels = input_ids.clone()
    labels[:, :-1] = input_ids[:, 1:]
    labels[:, -1] = -100
    return {"input_ids": input_ids.to(device), "labels": labels.to(device)}

# === Evaluate Perplexity ===
def eval_perplexity(model, inputs):
    model.eval()
    with torch.no_grad():
        loss = model(**inputs).loss
    return torch.exp(loss).item(), loss.item()

# === Run SmartSparse Experiment ===
def run_smartsparse_experiment():
    print("\n📚 Loading Calibration Data...")
    texts = load_wikitext2(num_samples=100)
    inputs = prepare_inputs(texts, tokenizer)

    base_ppl, base_loss = eval_perplexity(model, inputs)
    print(f"\n📊 Baseline Perplexity: {base_ppl:.2f} | Loss: {base_loss:.4f}")

    print("\n🔧 Running SmartSparse Pruning...")
    pruned_model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
    pruned_model.config.pad_token_id = tokenizer.eos_token_id
    pruner = SmartSparsePruner(pruned_model)
    pruner.compute_importance_scores(inputs)
    pruner.prune(sparsity=0.5)

    final_ppl, final_loss = eval_perplexity(pruned_model, inputs)
    print(f"\n🧠 Pruned Perplexity: {final_ppl:.2f} | Loss: {final_loss:.4f}")
    print(f"📉 Degradation: {final_ppl / base_ppl:.2f}x")

# ✅ Run It
run_smartsparse_experiment()

import matplotlib.pyplot as plt

# Results you already have
sparsity = 0.5
baseline_ppl = 8249.06
pruned_ppl = 3933.98
degradation = pruned_ppl / baseline_ppl

# Plot
plt.figure()
plt.bar(["Baseline", f"Pruned ({int(sparsity*100)}%)"], [baseline_ppl, pruned_ppl], alpha=0.7)
plt.ylabel("Perplexity")
plt.title("Perplexity Before and After Pruning")
plt.grid(axis='y')
plt.show()

# Degradation metric
degradation_ratio = pruned_ppl / baseline_ppl  # e.g., 0.48

plt.figure(figsize=(6, 1))
plt.barh(["Degradation"], [degradation_ratio], color='orange')
plt.xlim(0, 1)
plt.title("Degradation Ratio (Pruned / Baseline)")
plt.grid(axis='x')
plt.show()

import pandas as pd

metrics = {
    "Metric": ["Baseline PPL", "Baseline Loss", "Pruned PPL", "Pruned Loss", "Degradation (x)"],
    "Value": [baseline_ppl, 9.0179, pruned_ppl, 8.2774, round(degradation, 2)]
}

df = pd.DataFrame(metrics)
display(df)

def run_sparse_at_level(sparsity):
    print(f"\n🔧 Running SmartSparse Pruning at {int(sparsity*100)}% Sparsity...")
    pruned_model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
    pruned_model.config.pad_token_id = tokenizer.eos_token_id
    pruner = SmartSparsePruner(pruned_model)
    pruner.compute_importance_scores(inputs)  # reuse existing inputs
    pruner.prune(sparsity=sparsity)

    final_ppl, final_loss = eval_perplexity(pruned_model, inputs)
    print(f"🧠 Pruned PPL: {final_ppl:.2f} | Loss: {final_loss:.4f}")
    print(f"📉 Degradation: {final_ppl / base_ppl:.2f}x")

    return {"sparsity": sparsity, "pruned_ppl": final_ppl, "pruned_loss": final_loss,
            "degradation": final_ppl / base_ppl}
sparsity_levels = [0, 50]
perplexities = [baseline_ppl, pruned_ppl]

plt.figure(figsize=(6, 4))
plt.plot(sparsity_levels, perplexities, marker='o')
plt.title("SmartSparse Sweep (2-point View)")
plt.xlabel("Sparsity (%)")
plt.ylabel("Perplexity")
plt.grid(True)
plt.tight_layout()
plt.show()

# === Imports ===
import torch
import torch.nn as nn
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import load_dataset
import numpy as np

# === Load Calibration Data from Raw File ===
import urllib.request

# === Device Setup ===
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Device:", device)

# === Load OPT-125M ===
model_name = "facebook/opt-125m"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForCausalLM.from_pretrained(model_name)
tokenizer.pad_token = tokenizer.eos_token
model.config.pad_token_id = tokenizer.eos_token_id
model.to(device)

# === SmartSparse Pruner ===
class SmartSparsePruner:
    def __init__(self, model, alpha=1/3, beta=1/3, gamma=1/3):
        self.model = model
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.device = next(model.parameters()).device
        self.layerwise_scores = {}

    def _normalize(self, score):
        mean = score.mean()
        std = score.std() + 1e-8
        return (score - mean) / std

    def _compute_pqi(self, weight, bits=8):
        qmin, qmax = weight.min(), weight.max()
        scale = (qmax - qmin) / (2**bits - 1)
        if scale < 1e-9:
            return torch.zeros_like(weight)
        q_weight = torch.round((weight - qmin) / scale) * scale + qmin
        return (weight - q_weight).pow(2)

    def _compute_movement(self, grad):
        return grad.abs()

    def _compute_hessian(self, weight, grad):
        h_diag = grad.pow(2)
        return (weight.pow(2) / (2 * h_diag.clamp(min=1e-6)))

    def compute_importance_scores(self, inputs):
        self.model.zero_grad()
        input_ids = inputs["input_ids"].to(self.device)
        labels = inputs["labels"].to(self.device)
        loss = self.model(input_ids=input_ids, labels=labels).loss
        loss.backward()

        for name, module in self.model.named_modules():
            if isinstance(module, nn.Linear) and module.weight.grad is not None:
                w = module.weight.data
                g = module.weight.grad

                pqi = self._normalize(self._compute_pqi(w))
                move = self._normalize(self._compute_movement(g))
                hess = self._normalize(self._compute_hessian(w, g))
                fused = self.alpha * pqi + self.beta * move + self.gamma * hess

                self.layerwise_scores[name] = fused

    def prune(self, sparsity=0.5):
        all_scores = []
        module_map = {}
        for name, module in self.model.named_modules():
            if isinstance(module, nn.Linear) and name in self.layerwise_scores:
                scores = self.layerwise_scores[name].flatten()
                all_scores.append(scores)
                module_map[name] = module
        all_scores = torch.cat(all_scores)
        k = int((1 - sparsity) * all_scores.numel())
        threshold = torch.topk(all_scores, k, largest=True).values[-1]

        for name, module in module_map.items():
            mask = (self.layerwise_scores[name] >= threshold).float()
            module.weight.data.mul_(mask)

# === Load Calibration Data Safely ===
def load_wikitext2(num_samples=300):
    import urllib.request

    url = "https://raw.githubusercontent.com/pytorch/examples/master/word_language_model/data/wikitext-2/train.txt"
    file_path = "/tmp/wikitext-2-train.txt"
    urllib.request.urlretrieve(url, file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith('=')]

    # Group lines into ~paragraphs
    paragraphs = []
    buffer = ""
    for line in lines:
        buffer += line + " "
        if len(buffer.split()) > 100:
            paragraphs.append(buffer.strip())
            buffer = ""
        if len(paragraphs) >= num_samples:
            break

    return paragraphs

    # === Run SmartSparse with Custom Fusion Weights ===
def run_smartsparse_experiment(sparsity=0.5, alpha=1/3, beta=1/3, gamma=1/3):
    print(f"\n📚 Loading Calibration Data (α={alpha}, β={beta}, γ={gamma})...")
    texts = load_wikitext2(num_samples=100)
    inputs = prepare_inputs(texts, tokenizer)

    base_ppl, base_loss = eval_perplexity(model, inputs)
    print(f"\n📊 Baseline Perplexity: {base_ppl:.2f} | Loss: {base_loss:.4f}")

    print(f"\n🔧 Running SmartSparse Pruning (sparsity={int(sparsity*100)}%)...")
    pruned_model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
    pruned_model.config.pad_token_id = tokenizer.eos_token_id
    pruner = SmartSparsePruner(pruned_model, alpha=alpha, beta=beta, gamma=gamma)
    pruner.compute_importance_scores(inputs)
    pruner.prune(sparsity=sparsity)

    final_ppl, final_loss = eval_perplexity(pruned_model, inputs)
    degradation = final_ppl / base_ppl

    print(f"\n🧠 Pruned Perplexity: {final_ppl:.2f} | Loss: {final_loss:.4f}")
    print(f"📉 Degradation: {degradation:.2f}x")

    return {
        "α": alpha,
        "β": beta,
        "γ": gamma,
        "Baseline PPL": base_ppl,
        "Baseline Loss": base_loss,
        "Pruned PPL": final_ppl,
        "Pruned Loss": final_loss,
        "Degradation": degradation
    }

# === Ablation Variants ===
ablations = [
    {"alpha": 1.0, "beta": 0.0, "gamma": 0.0},  # Only PQI
    {"alpha": 0.0, "beta": 1.0, "gamma": 0.0},  # Only Movement
    {"alpha": 0.0, "beta": 0.0, "gamma": 1.0},  # Only Hessian
    {"alpha": 0.5, "beta": 0.5, "gamma": 0.0},  # PQI + Movement
    {"alpha": 0.0, "beta": 0.5, "gamma": 0.5},  # Movement + Hessian
    {"alpha": 0.5, "beta": 0.0, "gamma": 0.5},  # PQI + Hessian
    {"alpha": 1/3, "beta": 1/3, "gamma": 1/3},  # Uniform fusion (default)
]

# === Run All One at a Time to Avoid OOM ===
import pandas as pd
all_results = []
for config in ablations:
    result = run_smartsparse_experiment(
        sparsity=0.5,
        alpha=config["alpha"],
        beta=config["beta"],
        gamma=config["gamma"]
    )
    all_results.append(result)
    del result  # optional
    torch.cuda.empty_cache()  # optional

# === Visualize Results ===
df = pd.DataFrame(all_results)
display(df)

# === Plot ===
import matplotlib.pyplot as plt

plt.figure(figsize=(10, 5))
plt.bar(range(len(df)), df["Degradation"], tick_label=[
    f'{row["α"]:.1f}-{row["β"]:.1f}-{row["γ"]:.1f}' for _, row in df.iterrows()],
    color='orange', alpha=0.7)
plt.title("Fusion Weight Ablation vs. Degradation")
plt.xlabel("Fusion Weights (α-β-γ)")
plt.ylabel("Degradation (Pruned PPL / Baseline PPL)")
plt.grid(axis='y')
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()

# === IMPORTS ===
import torch, time
import numpy as np
import scipy.stats as stats
import matplotlib.pyplot as plt
from transformers import AutoModelForCausalLM, AutoTokenizer
import urllib.request
import pandas as pd
import torch.nn as nn

# === DEVICE ===
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Device:", device)

# === MODEL LOADING ===
model_name = "facebook/opt-125m"
tokenizer = AutoTokenizer.from_pretrained(model_name)
tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
model.config.pad_token_id = tokenizer.pad_token_id

# === DATA ===
def load_wikitext2(num_samples=300):
    url = "https://raw.githubusercontent.com/pytorch/examples/master/word_language_model/data/wikitext-2/train.txt"
    file_path = "/tmp/wikitext-2-train.txt"
    urllib.request.urlretrieve(url, file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith('=')]
    paragraphs, buffer = [], ""
    for line in lines:
        buffer += line + " "
        if len(buffer.split()) > 100:
            paragraphs.append(buffer.strip())
            buffer = ""
        if len(paragraphs) >= num_samples:
            break
    return paragraphs

def prepare_inputs(texts, tokenizer, max_len=64):
    enc = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_len)
    input_ids = enc["input_ids"]
    labels = input_ids.clone()
    labels[:, :-1] = input_ids[:, 1:]
    labels[:, -1] = -100
    return {"input_ids": input_ids.to(device), "labels": labels.to(device)}

# === METRICS ===
def eval_perplexity(model, inputs):
    model.eval()
    with torch.no_grad():
        loss = model(**inputs).loss
    return torch.exp(loss).item(), loss.item()

def profile_model_latency(model, tokenizer, prompt="The quick brown fox"):
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    torch.cuda.reset_peak_memory_stats()
    start = time.time()
    with torch.no_grad(): _ = model(**inputs)
    torch.cuda.synchronize()
    end = time.time()
    return end - start, torch.cuda.max_memory_allocated() / 1e6

def compute_entropy(t):
    probs = (t - t.min()) / (t.max() - t.min() + 1e-8)
    probs = probs.flatten()
    probs = probs / (probs.sum() + 1e-8)
    return -torch.sum(probs * torch.log(probs + 1e-8))

# === SMARTSPARSE PRUNER ===
class SmartSparsePruner:
    def __init__(self, model, alpha=-1, beta=-1, gamma=-1):
        self.model = model
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.device = next(model.parameters()).device
        self.layerwise_scores = {}

    def _normalize(self, score):
        mean = score.mean()
        std = score.std() + 1e-8
        return (score - mean) / std

    def _compute_pqi(self, weight, bits=8):
        qmin, qmax = weight.min(), weight.max()
        scale = (qmax - qmin) / (2**bits - 1)
        if scale < 1e-9: return torch.zeros_like(weight)
        q_weight = torch.round((weight - qmin) / scale) * scale + qmin
        return (weight - q_weight).pow(2)

    def _compute_movement(self, grad): return grad.abs()

    def _compute_hessian(self, weight, grad):
        h_diag = grad.pow(2)
        return (weight.pow(2) / (2 * h_diag.clamp(min=1e-6)))

    def compute_importance_scores(self, inputs):
        self.model.zero_grad()
        input_ids = inputs["input_ids"].to(self.device)
        labels = inputs["labels"].to(self.device)
        loss = self.model(input_ids=input_ids, labels=labels).loss
        loss.backward()

        for name, module in self.model.named_modules():
            if isinstance(module, nn.Linear) and module.weight.grad is not None:
                w, g = module.weight.data, module.weight.grad
                pqi = self._normalize(self._compute_pqi(w))
                move = self._normalize(self._compute_movement(g))
                hess = self._normalize(self._compute_hessian(w, g))

                if self.alpha == self.beta == self.gamma == -1:
                    E_pqi = compute_entropy(pqi)
                    E_move = compute_entropy(move)
                    E_hess = compute_entropy(hess)
                    weights = torch.tensor([1/E_pqi, 1/E_move, 1/E_hess])
                    noise = torch.randn(3) * 0.01  # add jitter
                    weights += noise
                    weights = torch.clamp(weights, min=0.01)
                    weights /= weights.sum()
                    self.alpha, self.beta, self.gamma = weights.tolist()

                fused = self.alpha * pqi + self.beta * move + self.gamma * hess
                self.layerwise_scores[name] = fused

    def prune(self, sparsity=0.5):
        all_scores, module_map = [], {}
        for name, module in self.model.named_modules():
            if isinstance(module, nn.Linear) and name in self.layerwise_scores:
                scores = self.layerwise_scores[name].flatten()
                all_scores.append(scores)
                module_map[name] = module
        all_scores = torch.cat(all_scores)
        k = int((1 - sparsity) * all_scores.numel())
        threshold = torch.topk(all_scores, k, largest=True).values[-1]
        for name, module in module_map.items():
            mask = (self.layerwise_scores[name] >= threshold).float()
            module.weight.data.mul_(mask)

# === EXPERIMENT RUN ===
def run_experiment(sparsity=0.5, alpha=-1, beta=-1, gamma=-1, seed=42):
    torch.manual_seed(seed)
    n_calib = 100 + np.random.randint(-10, 11)  # vary calibration set size
    texts = load_wikitext2(num_samples=n_calib)
    inputs = prepare_inputs(texts, tokenizer)
    base_ppl, base_loss = eval_perplexity(model, inputs)

    pruned_model = AutoModelForCausalLM.from_pretrained(model_name).to(device)
    pruned_model.config.pad_token_id = tokenizer.pad_token_id
    pruner = SmartSparsePruner(pruned_model, alpha, beta, gamma)
    pruner.compute_importance_scores(inputs)
    pruner.prune(sparsity=sparsity)

    pruned_ppl, pruned_loss = eval_perplexity(pruned_model, inputs)
    degradation = pruned_ppl / base_ppl
    latency, vram = profile_model_latency(pruned_model, tokenizer)

    return {
        "Seed": seed, "α": pruner.alpha, "β": pruner.beta, "γ": pruner.gamma,
        "Base PPL": base_ppl, "Pruned PPL": pruned_ppl, "Degradation": degradation,
        "Latency (s)": latency, "VRAM (MB)": vram
    }

# === MULTI-SEED EVALUATION ===
def run_multi_seed(n=5, **kwargs):
    results = [run_experiment(seed=42 + i, **kwargs) for i in range(n)]
    ppl = np.array([r["Pruned PPL"] for r in results])
    mean, std = ppl.mean(), ppl.std()
    ci = stats.t.interval(0.95, len(ppl)-1, loc=mean, scale=std/np.sqrt(len(ppl)))
    print(f"\n📊 Pruned PPL = {mean:.2f} ± {std:.2f} (95% CI: [{ci[0]:.2f}, {ci[1]:.2f}])")
    return pd.DataFrame(results)

# === RUN FULL ADAPTIVE EXPERIMENT ===
df_results = run_multi_seed(sparsity=0.5, alpha=-1, beta=-1, gamma=-1)
display(df_results)

import matplotlib.pyplot as plt

# === 📊 AUTO-PLOT SUMMARY ===
def plot_summary(df):
    fig, axs = plt.subplots(1, 3, figsize=(18, 5))

    # PPL bar plot
    axs[0].bar(df["Seed"], df["Pruned PPL"], color='steelblue', alpha=0.7)
    axs[0].set_title("Pruned Perplexity per Seed")
    axs[0].set_ylabel("PPL")
    axs[0].set_xlabel("Seed")

    # Degradation
    axs[1].plot(df["Seed"], df["Degradation"], marker='o', color='orange')
    axs[1].set_title("Degradation Ratio (Pruned / Base)")
    axs[1].set_ylabel("Ratio")
    axs[1].set_xlabel("Seed")
    axs[1].grid(True)

    # Fusion weights
    width = 0.25
    x = np.arange(len(df))
    axs[2].bar(x - width, df["α"], width, label='α (PQI)')
    axs[2].bar(x, df["β"], width, label='β (Movement)')
    axs[2].bar(x + width, df["γ"], width, label='γ (Hessian)')
    axs[2].set_title("Adaptive Fusion Weights")
    axs[2].set_xticks(x)
    axs[2].set_xticklabels(df["Seed"].tolist())
    axs[2].set_xlabel("Seed")
    axs[2].legend()

    plt.tight_layout()
    plt.show()

plot_summary(df_results)

# === ⚖️ BASELINE COMPARISON ===
def run_baseline(basename, alpha, beta, gamma, n=5):
    results = []
    for i in range(n):
        result = run_experiment(alpha=alpha, beta=beta, gamma=gamma, seed=100 + i)
        result["Method"] = basename
        results.append(result)
    return pd.DataFrame(results)

print("⏳ Running baseline: SAP (PQI only)...")
sap_df = run_baseline("SAP", 1.0, 0.0, 0.0)

print("⏳ Running baseline: Movement only...")
mov_df = run_baseline("Movement", 0.0, 1.0, 0.0)

print("⏳ Running baseline: Hessian only...")
hess_df = run_baseline("Hessian", 0.0, 0.0, 1.0)

# Combine all results
baseline_all = pd.concat([sap_df, mov_df, hess_df], ignore_index=True)

# === 📈 COMPARE BASELINES VS SMARTSPARSE ===
def plot_baseline_comparison(smart_df, baseline_df):
    methods = ["SAP", "Movement", "Hessian", "SmartSparse"]
    means = []
    stds = []

    for method in methods:
        if method == "SmartSparse":
            ppl = smart_df["Pruned PPL"]
        else:
            ppl = baseline_df[baseline_df["Method"] == method]["Pruned PPL"]
        means.append(ppl.mean())
        stds.append(ppl.std())

    plt.figure(figsize=(8, 5))
    plt.bar(methods, means, yerr=stds, capsize=8, color='mediumseagreen')
    plt.ylabel("Pruned PPL")
    plt.title("Baseline Comparison: Mean ± Std (n=5)")
    plt.grid(axis='y')
    plt.tight_layout()
    plt.show()

plot_baseline_comparison(df_results, baseline_all)

# Optional: display summary table
summary_table = baseline_all.groupby("Method")[["Pruned PPL", "Degradation"]].agg(["mean", "std"]).round(2)
display(summary_table)