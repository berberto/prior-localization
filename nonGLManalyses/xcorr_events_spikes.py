import numpy as np
import pandas as pd
import brainbox.io.one as bbone
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.signal import correlate, correlation_lags
from sklearn.preprocessing import normalize
from brainbox.processing import bincount2D
from brainbox.metrics.single_units import quick_unit_metrics
from iblutil.util import Bunch
from one.api import ONE

# which sesssion and probe to look at, bin size
BINWIDTH = 0.02
CORRWIND = (-0.2, 0.8)  # seconds
MIN_RATE = 1.  # Minimum rate, in hertz, for a neuron to be included in xcorr analysis

# Do some data loading
one = ONE()


# Build a basic vector to work with and also bin spikes
def binf(t):
    return np.ceil(t / BINWIDTH).astype(int)


def load_sess(eid, probe):
    """
    Fetch spike info and 
    """
    one = ONE()
    spikes, _ = bbone.load_spike_sorting(eid, probe=probe, one=one,
                                            dataset_types=[
                                                'spikes.times',
                                                'spikes.clusters',
                                                'spikes.amps',
                                                'spikes.depths'
                                            ])
    trialsdf = bbone.load_trials_df(eid, one=one, addtl_types=['firstMovement_times'])
    return spikes[probe], trialsdf


def get_spikes_events(spikes, trialsdf, passing_fraction=0.):
    if passing_fraction > 0:
        metrics = quick_unit_metrics(spikes.clusters,
                                     spikes.times,
                                     spikes.amps,
                                     spikes.depths)
        pass_units = np.argwhere(metrics.label >= passing_fraction).squeeze()
        passmask = np.isin(spikes.clusters, pass_units)
        spikes = Bunch({k: v[passmask] for k, v in spikes.items()})


    # Get information about the details of our session such as start time etc
    t_start = 0
    t_end = trialsdf['trial_end'].max()

    events = {
        'leftstim': trialsdf[trialsdf.contrastLeft.notna()].stimOn_times,
        'rightstim': trialsdf[trialsdf.contrastRight.notna()].stimOn_times,
        'gocue': trialsdf.goCue_times,
        'movement': trialsdf.firstMovement_times,
        'correct': trialsdf[trialsdf.feedbackType == 1].feedback_times,
        'incorrect': trialsdf[trialsdf.feedbackType == -1].feedback_times,
    }
    return spikes, t_start, t_end, events


def get_binned(spikes, t_start, t_end):
    tmask = spikes.times < t_end  # Only get spikes in interval
    binned = bincount2D(spikes.times[tmask], spikes.clusters[tmask],
                        xlim=[t_start, t_end],
                        xbin=BINWIDTH)[0]
    ratemask = np.argwhere(np.mean(binned, axis=1) >= (BINWIDTH * MIN_RATE)).squeeze()
    binned = binned[ratemask]
    return binned


def get_event_vec(t_start, t_end, event_times):
    vecshape = binf(t_end + BINWIDTH) - binf(t_start)
    evec = np.zeros(vecshape)
    evinds = event_times.dropna().apply(binf)
    evec[evinds] = 1
    return evec


def xcorr_window(binned, evec):
    lags = correlation_lags(evec.shape[0], binned.shape[1]) * BINWIDTH  # Value of correlation lags
    start, end = np.searchsorted(lags, CORRWIND[0]), np.searchsorted(lags, CORRWIND[1]) + 1
    lagvals = lags[start:end]  # Per-step values of the lag
    corrarr = np.zeros((binned.shape[0], end - start))
    for i in range(binned.shape[0]):
        corrarr[i] = correlate(evec, binned[i])[start:end]
    return corrarr, lagvals


def heatmap_xcorr(corrarr, lagvals, ax=None, norm=True):
    ax = ax if ax is not None else plt.subplots(1, 1)[1]
    normarr = normalize(corrarr) if norm else corrarr
    sortinds = np.argsort(normarr.argmax(axis=1))
    sns.heatmap(pd.DataFrame(normarr[sortinds], columns=lagvals), ax=ax)
    return ax


if __name__ == "__main__":
    from ..decoding.decoding_utils import query_sessions
    from dask.distributed import Client
    from dask_jobqueue import SLURMCluster

    sessions = query_sessions('aligned-behavior')
    N_CORES = 1
    cluster = SLURMCluster(cores=N_CORES, memory='12GB', processes=1, queue="shared-cpu",
                           walltime="01:15:00",
                           interface='ib0',
                           extra=["--lifetime", "70m", "--lifetime-stagger", "4m"],
                           job_cpu=N_CORES, env_extra=[f'export OMP_NUM_THREADS={N_CORES}',
                                                       f'export MKL_NUM_THREADS={N_CORES}',
                                                       f'export OPENBLAS_NUM_THREADS={N_CORES}'])
    cluster.adapt(minimum_jobs=0, maximum_jobs=400)
    client = Client(cluster)

    filefutures = client.map(load_sess, sessions['eid'], sessions['probe'])