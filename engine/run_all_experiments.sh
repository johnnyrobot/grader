#!/bin/bash
# Run all 4 calibration experiments sequentially to avoid GPU contention.
set -e
cd /Users/johnnyrobot/code/grader

echo "############################################################"
echo "# Experiment 1: pointer, grader-text:8b, k=6"
echo "############################################################"
uv run python -u engine/calibrate.py \
  --model grader-text:8b --question 'pointer' \
  --n-test 20 --n-exemplars 30 --k 6 \
  --out engine/results_pointer_k6.json 2>&1 | tee engine/output_pointer_k6.txt

echo ""
echo "############################################################"
echo "# Experiment 2: pointer, qwen3:8b uncapped, k=6"
echo "############################################################"
uv run python -u engine/calibrate.py \
  --model qwen3:8b --question 'pointer' \
  --n-test 20 --n-exemplars 30 --k 6 \
  --out engine/results_pointer_uncapped_k6.json 2>&1 | tee engine/output_pointer_uncapped_k6.txt

echo ""
echo "############################################################"
echo "# Experiment 3: pointer, grader-text:8b, k=8"
echo "############################################################"
uv run python -u engine/calibrate.py \
  --model grader-text:8b --question 'pointer' \
  --n-test 20 --n-exemplars 30 --k 8 \
  --out engine/results_pointer_k8.json 2>&1 | tee engine/output_pointer_k8.txt

echo ""
echo "############################################################"
echo "# Experiment 4: pointer, grader-text:8b, k=12"
echo "############################################################"
uv run python -u engine/calibrate.py \
  --model grader-text:8b --question 'pointer' \
  --n-test 20 --n-exemplars 30 --k 12 \
  --out engine/results_pointer_k12.json 2>&1 | tee engine/output_pointer_k12.txt

echo ""
echo "############################################################"
echo "# ALL EXPERIMENTS COMPLETE"
echo "############################################################"
for f in engine/results_pointer_k6.json engine/results_pointer_uncapped_k6.json engine/results_pointer_k8.json engine/results_pointer_k12.json; do
  if [ -f "$f" ]; then
    echo "--- $f ---"
    python3 -c "
import json
d = json.load(open('$f'))
ceiling = d['human_ceiling']
base = d['arms'][0]
anch = d['arms'][1]
print(f'  model={d[\"model\"]} k={d[\"k\"]} n_test={d[\"n_test\"]}')
print(f'  human ceiling: QWK={ceiling[\"qwk\"]:.3f}')
print(f'  baseline:      QWK={base[\"qwk\"]:.3f} exact={base[\"exact\"]:.0%} bias={base[\"mean_error\"]:+.2f}')
print(f'  anchored:      QWK={anch[\"qwk\"]:.3f} exact={anch[\"exact\"]:.0%} bias={anch[\"mean_error\"]:+.2f}')
print(f'  lift: {d[\"anchor_lift_qwk\"]:+.3f} | ceiling reached: {d[\"pct_of_human_ceiling\"]:.0f}%')
"
  fi
done