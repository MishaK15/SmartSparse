# SmartSparse

**SmartSparse** is a one-shot pruning framework for large language models (LLMs) that fuses three orthogonal signals—quantization error (PQI), gradient movement, and Hessian curvature—into a unified importance score. This fusion enables efficient, interpretable sparsification without requiring fine-tuning.

## Key Features
- 🧠 **Signal Fusion**: Integrates structural, dynamic, and curvature-based importance metrics.
- ⚡ **One-Shot**: No iterative fine-tuning required.
- 📉 **High Compression**: Achieves strong sparsity-performance tradeoffs.
- 📚 **LLM-Scalable**: Tested on OPT models and WikiText2.

## Method Overview
![SmartSparse Comparison](https://github.com/user-attachments/assets/77ff672a-65f0-47fc-8d07-6eb828ecfb38)

## Results
![SmartSparse Results](https://github.com/user-attachments/assets/8868310f-f33f-4e05-8c83-0ebef7cd7308)

## Getting Started

To try SmartSparse, run the core script:

```bash
python finalsmartsparse.py
