#!/usr/bin/env bash
# Run only the public TREC fielded-retrieval benchmark on an ephemeral GPU host.
# This script never downloads patient data and does not start or terminate EC2.

set -Eeuo pipefail

# Session Manager can start a deliberately minimal shell.  Preserve any
# image-provided runtime path (the AWS deep-learning image keeps Python 3.13
# there), then add standard system locations for package commands.
export PATH="${PATH:+${PATH}:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <private-s3-bucket>" >&2
  exit 2
fi

bucket="$1"
workdir="/opt/trial-matcher-fielded-benchmark"
# Keep the runner's own log separate from an interactive launch log.  This
# avoids two processes competing for the same file when launched with nohup.
log_path="/var/tmp/trec-fielded-benchmark.log"
result_key="results/trec-fielded-weighted-rrf.json"
log_key="results/trec-fielded-weighted-rrf.log"

upload_log() {
  local status=$?
  if command -v aws >/dev/null 2>&1; then
    aws s3 cp "$log_path" "s3://${bucket}/${log_key}" --only-show-errors || true
  fi
  exit "$status"
}

exec > >(tee -a "$log_path") 2>&1
trap upload_log EXIT

install_with_system_package_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq "$@"
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    dnf install -y -q "$@"
    return
  fi

  echo "Neither apt-get nor dnf is available to install required packages." >&2
  exit 1
}

if ! command -v aws >/dev/null 2>&1; then
  install_with_system_package_manager awscli
fi

if ! python3 -c "import venv"; then
  if command -v apt-get >/dev/null 2>&1; then
    install_with_system_package_manager python3-venv
  else
    install_with_system_package_manager python3
  fi
fi

rm -rf "$workdir"
mkdir -p "$workdir"
aws s3 cp \
  "s3://${bucket}/source/trial-matcher-fielded-source.tgz" \
  "$workdir/source.tgz" \
  --only-show-errors
tar -xzf "$workdir/source.tgz" -C "$workdir"
aws s3 sync \
  "s3://${bucket}/trec/raw/" \
  "$workdir/datasets/evaluation/trec/raw/" \
  --only-show-errors

cd "$workdir"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

nvidia-smi
.venv/bin/python -c \
  "from huggingface_hub import snapshot_download; snapshot_download(repo_id='NeuML/pubmedbert-base-embeddings', revision='b79526d6ef3645e0df4530322e266f24c829f5ef')"

PYTHONPATH="$workdir" .venv/bin/python scripts/build_trec_semantic_index.py \
  --output-dir datasets/evaluation/trec/semantic-fielded \
  --batch-size 128
PYTHONPATH="$workdir" .venv/bin/python scripts/evaluate_trec_hybrid.py \
  --semantic-dir datasets/evaluation/trec/semantic-fielded \
  --semantic-field-fusion weighted-rrf \
  --output "$workdir/trec-fielded-weighted-rrf.json"

aws s3 cp \
  "$workdir/trec-fielded-weighted-rrf.json" \
  "s3://${bucket}/${result_key}" \
  --only-show-errors
echo "Benchmark completed: s3://${bucket}/${result_key}"
