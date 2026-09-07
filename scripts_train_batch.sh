#!/bin/zsh
# Train a list of pairs across every timeframe, RESUMABLY.
#
# Safe to stop at any time (Ctrl-C, a shutdown, a closed lid) and safe to run
# again afterwards: a timeframe whose two model files already exist is skipped,
# and every download is cached on disk through a .part file and a rename, so
# an interrupted fetch leaves nothing half-written to trip over later.
#
#     ./scripts_train_batch.sh                 # the ten-coin batch
#     ./scripts_train_batch.sh BTCUSDT ETHUSDT # or whichever you name
setopt NULL_GLOB
cd "$(dirname "$0")"

# NOT `${@:-a b c}` — zsh expands that default as ONE element containing the
# whole string, so the batch ran `train.py --symbol "XRPUSDT ZECUSDT ..."`,
# failed instantly and reported BATCH DONE in one second.
if [ $# -gt 0 ]; then
  COINS=("$@")
else
  COINS=(XRPUSDT ZECUSDT HYPEUSDT DOGEUSDT BNBUSDT ENAUSDT UNIUSDT NEARUSDT SUIUSDT 1000PEPEUSDT)
fi
INTERVALS=(1m 5m 15m 1h 4h 1d)
LOGDIR=${TRAIN_LOGS:-train_logs}
mkdir -p $LOGDIR

for S in $COINS; do
  todo=()
  for IV in $INTERVALS; do
    # -f, not a glob. These paths contain no wildcard, so assigning them to an
    # array yields the literal string whether or not the file exists — which
    # made a first draft of this report every pair as already complete and
    # skip the entire batch.
    if [ ! -f output/judge_${S}_${IV}_h1.joblib ] \
    || [ ! -f output/judge_${S}_${IV}_h2.joblib ]; then todo+=($IV); fi
  done
  if [ ${#todo} -eq 0 ]; then
    echo "=== $S already complete, skipping"
    continue
  fi
  echo "=== $S starting $(date -u +%H:%M:%S)  (${(j:,:)todo})"
  # --no-tape by default. The trade-tape backfill is ~99% of a training run
  # and an eight-fit ablation put its contribution at -0.002 AUC, smaller
  # than the fold spread of every single fit it was measured against. Set
  # WITH_TAPE=1 to pay for it anyway.
  TAPE_ARG=--no-tape
  [ -n "$WITH_TAPE" ] && TAPE_ARG=
  python3 train.py --symbol $S --intervals ${(j:,:)todo} $TAPE_ARG >> $LOGDIR/$S.log 2>&1
  echo "=== $S done rc=$? $(date -u +%H:%M:%S)"
  grep -E "h[12] \(" $LOGDIR/$S.log | tail -12
done
echo "BATCH DONE $(date -u +%H:%M:%S)"
