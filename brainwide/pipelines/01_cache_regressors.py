# Standard library
import hashlib
import logging
import os
import pickle
import re
from datetime import datetime as dt
from pathlib import Path
from utils import load_primaries
from utils import cache_primaries

# Third party libraries
import dask
import numpy as np
import pandas as pd
from dask.distributed import Client
from dask_jobqueue import SLURMCluster

# IBL libraries
import brainbox.io.one as bbone
from one.api import ONE
from brainwide.params import CACHE_PATH

# Brainwide repo imports
from brainwide.utils import query_sessions, get_impostor_df

_logger = logging.getLogger('brainwide')

@dask.delayed
def delayed_load(session_id, probes, params, force_load=False):
    try:
        return load_primaries(session_id, probes, **params)
    except KeyError as e:
        if force_load:
            params['resolved_alignment'] = False
            return load_primaries(session_id, probes, **params)
        else:
            raise e


@dask.delayed(pure=False, traverse=False)
def delayed_save(subject, session_id, probes, params, outputs):
    return cache_primaries(subject, session_id, probes, params, *outputs)


# Parameters
SESS_CRITERION = 'resolved-behavior'
DATE = str(dt.today())
MAX_LEN = 2.
T_BEF = 0.6
T_AFT = 0.6
BINWIDTH = 0.02
ABSWHEEL = True
QC = True
EPHYS_IMPOSTOR = False
FORCE = True  # If load_spike_sorting_fast doesn't return _channels, use _channels function
# End parameters

# Construct params dict from above
params = {
    'max_len': MAX_LEN,
    't_before': T_BEF,
    't_after': T_AFT,
    'binwidth': BINWIDTH,
    'abswheel': ABSWHEEL,
    'resolved_alignment': True if re.match('resolved.*', SESS_CRITERION) else False,
    'ret_qc': QC
}

one = ONE()
dataset_futures = []

sessdf = query_sessions(SESS_CRITERION).set_index(['subject', 'eid'])

for eid in sessdf.index.unique(level='eid'):
    xsdf = sessdf.xs(eid, level='eid')
    subject = xsdf.index[0]
    probes = xsdf.pid.to_list()
    load_outputs = delayed_load(eid, probes, params, force_load=FORCE)
    save_future = delayed_save(subject, eid, probes, params, load_outputs)
    dataset_futures.append([subject, eid, probes, save_future])

N_CORES = 4
cluster = SLURMCluster(cores=N_CORES,
                       memory='32GB',
                       processes=1,
                       queue="shared-cpu",
                       walltime="01:15:00",
                       log_directory='/home/gercek/dask-worker-logs',
                       interface='ib0',
                       extra=["--lifetime", "60m", "--lifetime-stagger", "10m"],
                       job_cpu=N_CORES,
                       env_extra=[
                           f'export OMP_NUM_THREADS={N_CORES}',
                           f'export MKL_NUM_THREADS={N_CORES}',
                           f'export OPENBLAS_NUM_THREADS={N_CORES}'
                       ])
cluster.scale(20)
client = Client(cluster)

tmp_futures = [client.compute(future[3]) for future in dataset_futures]
params['maxlen'] = params['max_len']
params.pop('max_len')
impostor_df = get_impostor_df(
    '',
    one,
    ephys=EPHYS_IMPOSTOR,
    tdf_kwargs={k: v for k, v in params.items() if k not in ['resolved_alignment', 'ret_qc']},
    ret_template=True)

# Run below code AFTER futures have finished!
dataset = [{
    'subject': x[0],
    'eid': x[1],
    'probes': x[2],
    'meta_file': tmp_futures[i].result()[0],
    'reg_file': tmp_futures[i].result()[1]
} for i, x in enumerate(dataset_futures) if tmp_futures[i].status == 'finished']
dataset = pd.DataFrame(dataset)

outdict = {'params': params, 'dataset_filenames': dataset}
with open(Path(CACHE_PATH).joinpath(DATE + '_dataset_metadata.pkl'), 'wb') as fw:
    pickle.dump(outdict, fw)
