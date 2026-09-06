#!/bin/zsh
# Train without cooking the laptop.
#
# `taskpolicy -b` sets BACKGROUND QoS, which on Apple Silicon confines the
# process (and its children) to the EFFICIENCY cores. Those are the cores the
# machine uses when it is idle; they are slower and draw a fraction of the
# power, so the fans stay down. Children inherit it, so the whole batch is
# covered by the one call.
#
# The thread caps matter as much. LightGBM and the BLAS underneath numpy both
# default to "every core you have", which is how a single fit can light up all
# ten and heat the chassis for the sake of a job nobody is waiting on.
export OMP_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=2
export MKL_NUM_THREADS=2
export VECLIB_MAXIMUM_THREADS=2
export NUMEXPR_NUM_THREADS=2
exec taskpolicy -b nice -n 10 ./scripts_train_batch.sh "$@"
