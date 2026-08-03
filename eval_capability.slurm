#!/bin/bash
#SBATCH --job-name=cap-eval
#SBATCH --partition=gpu
#SBATCH --qos=qos_dijk
#SBATCH --account=dijk
#SBATCH --gpus=h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=08:00:00
#SBATCH --output=/gpfs/radev/home/ewl33/scratch/logs/cap-eval-%j.out

set -euo pipefail

REPO=$HOME/project/target-id-models
BIN=$REPO/envs/qwensft/bin

# project fileset is at its group inode limit -- everything goes to scratch
export HF_HOME=$HOME/scratch/hf_cache
export HF_HUB_OFFLINE=1
export TORCHDYNAMO_DISABLE=1          # torch.compile stalls Qwen3.5 on inductor
export TOKENIZERS_PARALLELISM=false

# causal_conv1d_cuda.so needs GLIBCXX_3.4.29, absent from the node's system
# libstdc++; this miniconda lib has it. Without it the import fails at model load.
export LD_LIBRARY_PATH=/gpfs/radev/apps/avx512/software/miniconda/24.3.0-miniconda/lib:${LD_LIBRARY_PATH:-}

# node CA store is stale
export SSL_CERT_FILE=$("$BIN/python" -c 'import certifi;print(certifi.where())')
export REQUESTS_CA_BUNDLE=$SSL_CERT_FILE

: "${MODEL:?set MODEL}"
: "${LABEL:?set LABEL}"
OUT=${OUT:-$HOME/scratch/capability_${LABEL}.json}
PER_CAT=${PER_CAT:-50}
BATCH=${BATCH:-16}
# GPQA reasoning runs long; a shared budget truncates it. Separate budgets.
MAXNEW_GPQA=${MAXNEW_GPQA:-16384}
MAXNEW_MMLU=${MAXNEW_MMLU:-8192}

mkdir -p "$HOME/scratch/logs"
echo "=== $LABEL | $MODEL -> $OUT ==="
nvidia-smi || true                    # bare `| head` SIGPIPEs under pipefail

"$BIN/python" -c "import torch,fla,causal_conv1d; print('preflight ok', torch.__version__)"

exec "$BIN/python" "$REPO/scripts/eval_capability.py" \
  --model "$MODEL" --label "$LABEL" --out "$OUT" \
  --per-category "$PER_CAT" --batch-size "$BATCH" \
  --max-new-gpqa "$MAXNEW_GPQA" --max-new-mmlu "$MAXNEW_MMLU" \
  --benchmarks "${BENCH:-gpqa,mmlu}" ${NOTHINK:+--no-think} \
  --limit "${LIMIT:-0}"
