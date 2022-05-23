from pathlib import Path
from behavior_models.models.utils import format_data as format_data_mut
from behavior_models.models.utils import format_input as format_input_mut
from behavior_models.models import expSmoothing_prevAction, expSmoothing_stimside
import os
import numpy as np
import torch
import pandas as pd
from brainbox.task.closed_loop import generate_pseudo_blocks, _draw_position, _draw_contrast
from braindelphi.decoding.functions.nulldistributions import generate_imposter_session
from braindelphi.decoding.functions.utils import check_bhv_fit_exists

possible_targets = ['choice', 'feedback', 'signcont', 'pLeft']

def compute_beh_target(trials_df, metadata, remove_old=False, **kwargs):
    """
    Computes regression target for use with regress_target, using subject, eid, and a string
    identifying the target parameter to output a vector of N_trials length containing the target

    Parameters
    ----------
    target : str
        String in ['prior', 'prederr', 'signcont'], indication model-based prior, prediction error,
        or simple signed contrast per trial
    subject : str
        Subject identity in the IBL database, e.g. KS022
    eids_train : list of str
        list of UUID identifying sessions on which the model is trained.
    eids_test : str
        UUID identifying sessions on which the target signal is computed
    savepath : str
        where the beh model outputs are saved
    behmodel : str
        behmodel to use
    pseudo : bool
        Whether or not to compute a pseudosession result, rather than a real result.
    modeltype : behavior_models model object
        Instantiated object of behavior models. Needs to be instantiated for pseudosession target
        generation in the case of a 'prior' or 'prederr' target.
    beh_data : behavioral data feed to the model when using pseudo-sessions

    Returns
    -------
    pandas.Series
        Pandas series in which index is trial number, and value is the target
    """
    if kwargs['target'] not in possible_targets:
        raise ValueError('target should be in {}'.format(possible_targets))

    '''
    load/fit a behavioral model to compute target on a single session
    Params:
        eids_train: list of eids on which we train the network
        eid_test: eid on which we want to compute the target signals, only one string
        beh_data_test: if you have to launch the model on beh_data_test.
                       if beh_data_test is explicited, the eid_test will not be considered
        target can be pLeft or signcont. If target=pLeft, it will return the prior predicted by modeltype
                                         if modetype=None, then it will return the actual pLeft (.2, .5, .8)
    '''

    # check if is trained
    eids_train = ([metadata['eid']] if 'eids_train' not in metadata.keys()
                   else metadata['eids_train'])

    istrained, fullpath = check_bhv_fit_exists(metadata['subject'], kwargs['model'], eids_train, kwargs['modelfit_path'])

    if kwargs['target'] == 'signcont':
        if 'signedContrast' in trials_df.keys():
            out = trials_df['signedContrast']
        else:
            out = np.nan_to_num(trials_df.contrastLeft) - np.nan_to_num(trials_df.contrastRight)
        return out
    if kwargs['target'] == 'choice':
        return trials_df.choice.values
    if kwargs['target'] == 'feedback':
        return trials_df.feedbackType.values
    elif (kwargs['target'] == 'pLeft') and (kwargs['model'] is None):
        return trials_df.probabilityLeft.values
    elif (kwargs['target'] == 'pLeft') and (kwargs['model'] is optimal_Bayesian):  # bypass fitting and generate priors
        side, stim, act, _ = format_data_mut(trials_df)
        signal = optimal_Bayesian(act, stim, side)
        return signal.numpy().squeeze()

    if (not istrained) and (kwargs['target'] != 'signcont') and (kwargs['model'] is not None):
        datadict = {'stim_side': [], 'actions': [], 'stimuli': []}
        if 'eids_train' in kwargs.keys() or len(eids_train) >= 2:
            raise NotImplementedError('Sorry, this features is not implemented yet')
        for _ in eids_train:  # this seems superfluous but this is a relevant structure for when eids_train != [eid]
            side, stim, act, _ = format_data_mut(trials_df)
            datadict['stim_side'].append(side)
            datadict['stimuli'].append(stim)
            datadict['actions'].append(act)
        stimuli, actions, stim_side = format_input_mut(datadict['stimuli'], datadict['actions'], datadict['stim_side'])
        model = kwargs['model'](kwargs['modelfit_path'], np.array(eids_train), metadata['subject'],
                                actions, stimuli, stim_side)
        model.load_or_train(remove_old=remove_old)
    elif (kwargs['target'] != 'signcont') and (kwargs['model'] is not None):
        model = kwargs['model'](kwargs['modelfit_path'],
                                eids_train,
                                metadata['subject'],
                                actions=None,
                                stimuli=None,
                                stim_side=None)
        model.load_or_train(loadpath=str(fullpath))

    # compute signal
    stim_side, stimuli, actions, _ = format_data_mut(trials_df)
    stimuli, actions, stim_side = format_input_mut([stimuli], [actions], [stim_side])
    signal = model.compute_signal(signal='prior' if kwargs['target'] == 'pLeft' else kwargs['target'],
                                  act=actions,
                                  stim=stimuli,
                                  side=stim_side)['prior' if kwargs['target'] == 'pLeft' else kwargs['target']]

    tvec = signal.squeeze()
    if kwargs['binarization_value'] is not None:
        tvec = (tvec > kwargs['binarization_value']) * 1

    return tvec


def optimal_Bayesian(act, stim, side):
    '''
    Generates the optimal prior
    Params:
        act (array of shape [nb_sessions, nb_trials]): action performed by the mice of shape
        side (array of shape [nb_sessions, nb_trials]): stimulus side (-1 (right), 1 (left)) observed by the mice
    Output:
        prior (array of shape [nb_sessions, nb_chains, nb_trials]): prior for each chain and session
    '''
    act = torch.from_numpy(act)
    side = torch.from_numpy(side)
    lb, tau, ub, gamma = 20, 60, 100, 0.8
    nb_blocklengths = 100
    nb_typeblocks = 3
    eps = torch.tensor(1e-15)

    alpha = torch.zeros([act.shape[-1], nb_blocklengths, nb_typeblocks])
    alpha[0, 0, 1] = 1
    alpha = alpha.reshape(-1, nb_typeblocks * nb_blocklengths)
    h = torch.zeros([nb_typeblocks * nb_blocklengths])

    # build transition matrix
    b = torch.zeros([nb_blocklengths, nb_typeblocks, nb_typeblocks])
    b[1:][:, 0, 0], b[1:][:, 1, 1], b[1:][:, 2, 2] = 1, 1, 1  # case when l_t > 0
    b[0][0][-1], b[0][-1][0], b[0][1][np.array([0, 2])] = 1, 1, 1. / 2  # case when l_t = 1
    n = torch.arange(1, nb_blocklengths + 1)
    ref = torch.exp(-n / tau) * (lb <= n) * (ub >= n)
    torch.flip(ref.double(), (0,))
    hazard = torch.cummax(
        ref / torch.flip(torch.cumsum(torch.flip(ref.double(), (0,)), 0) + eps, (0,)), 0)[0]
    l = torch.cat(
        (torch.unsqueeze(hazard, -1),
         torch.cat((torch.diag(1 - hazard[:-1]), torch.zeros(nb_blocklengths - 1)[None]), axis=0)),
        axis=-1)  # l_{t-1}, l_t
    transition = eps + torch.transpose(l[:, :, None, None] * b[None], 1, 2).reshape(
        nb_typeblocks * nb_blocklengths, -1)

    # likelihood
    lks = torch.hstack([
        gamma * (side[:, None] == -1) + (1 - gamma) * (side[:, None] == 1),
        torch.ones_like(act[:, None]) * 1. / 2,
        gamma * (side[:, None] == 1) + (1 - gamma) * (side[:, None] == -1)
    ])
    to_update = torch.unsqueeze(torch.unsqueeze(act.not_equal(0), -1), -1) * 1

    for i_trial in range(act.shape[-1]):
        # save priors
        if i_trial > 0:
            alpha[i_trial] = torch.sum(torch.unsqueeze(h, -1) * transition, axis=0) * to_update[i_trial - 1] \
                             + alpha[i_trial - 1] * (1 - to_update[i_trial - 1])
        h = alpha[i_trial] * lks[i_trial].repeat(nb_blocklengths)
        h = h / torch.unsqueeze(torch.sum(h, axis=-1), -1)

    predictive = torch.sum(alpha.reshape(-1, nb_blocklengths, nb_typeblocks), 1)
    Pis = predictive[:, 0] * gamma + predictive[:, 1] * 0.5 + predictive[:, 2] * (1 - gamma)

    return 1 - Pis

def get_target_pLeft(nb_trials,
                     nb_sessions,
                     take_out_unbiased,
                     bin_size_kde,
                     subjModel=None,
                     antithetic=True):
    # if subjModel is empty, compute the optimal Bayesian prior
    if subjModel is not None:
        istrained, fullpath = check_bhv_fit_exists(subjModel['subject'], subjModel['modeltype'],
                                                   subjModel['subjeids'],
                                                   subjModel['modelfit_path'].as_posix() + '/')
        if not istrained:
            raise ValueError('Something is wrong. The model should be trained by this line')
        model = subjModel['modeltype'](subjModel['modelfit_path'].as_posix() + '/',
                                       subjModel['subjeids'],
                                       subjModel['subject'],
                                       actions=None,
                                       stimuli=None,
                                       stim_side=None)
        model.load_or_train(loadpath=str(fullpath))
    else:
        model = None
    contrast_set = np.array([0., 0.0625, 0.125, 0.25, 1])
    target_pLeft = []
    for _ in np.arange(nb_sessions):
        if model is None or not subjModel['use_imposter_session_for_balancing']:
            pseudo_trials = pd.DataFrame()
            pseudo_trials['probabilityLeft'] = generate_pseudo_blocks(nb_trials)
            for i in range(pseudo_trials.shape[0]):
                position = _draw_position([-1, 1], pseudo_trials['probabilityLeft'][i])
                contrast = _draw_contrast(contrast_set, 'uniform')
                if position == -1:
                    pseudo_trials.loc[i, 'contrastLeft'] = contrast
                elif position == 1:
                    pseudo_trials.loc[i, 'contrastRight'] = contrast
                pseudo_trials.loc[i, 'stim_side'] = position
            pseudo_trials['signed_contrast'] = pseudo_trials['contrastRight']
            pseudo_trials.loc[pseudo_trials['signed_contrast'].isnull(),
                              'signed_contrast'] = -pseudo_trials['contrastLeft']
            pseudo_trials['choice'] = np.NaN  # choice padding
        else:
            pseudo_trials = generate_imposter_session(subjModel['imposterdf'],
                                                      subjModel['eid'],
                                                      nb_trials,
                                                      nbSampledSess=10)
        side, stim, act, _ = mut.format_data(pseudo_trials)
        if model is None:
            msub_pseudo_tvec = optimal_Bayesian(act.values, stim, side.values)
        elif not subjModel['use_imposter_session_for_balancing']:
            arr_params = model.get_parameters(parameter_type='posterior_mean')[None]
            valid = np.ones([1, pseudo_trials.index.size], dtype=bool)
            stim, act, side = mut.format_input([stim], [act.values], [side.values])
            act_sim, stim, side = model.simulate(arr_params,
                                                 stim,
                                                 side,
                                                 torch.from_numpy(valid),
                                                 nb_simul=10,
                                                 only_perf=False)
            act_sim = act_sim.squeeze().T
            stim = torch.tile(stim.squeeze()[None], (act_sim.shape[0], 1))
            side = torch.tile(side.squeeze()[None], (act_sim.shape[0], 1))
            msub_pseudo_tvec = model.compute_signal(
                signal=('prior' if subjModel['target'] == 'pLeft' else subjModel['target']),
                act=act_sim,
                stim=stim,
                side=side)
            msub_pseudo_tvec = msub_pseudo_tvec['prior'].T
        else:
            stim, act, side = mut.format_input([stim], [act.values], [side.values])
            msub_pseudo_tvec = model.compute_signal(
                signal=('prior' if subjModel['target'] == 'pLeft' else subjModel['target']),
                act=act,
                stim=stim,
                side=side)
            msub_pseudo_tvec = msub_pseudo_tvec['prior' if subjModel['target'] ==
                                                'pLeft' else subjModel['target']]
        if take_out_unbiased:
            target_pLeft.append(
                msub_pseudo_tvec[(pseudo_trials.probabilityLeft != 0.5).values].ravel())
        else:
            target_pLeft.append(msub_pseudo_tvec.ravel())
    target_pLeft = np.concatenate(target_pLeft)
    if antithetic:
        target_pLeft = np.concatenate([target_pLeft, 1 - target_pLeft])
    out = np.histogram(target_pLeft,
                       bins=(np.arange(-bin_size_kde, 1 + bin_size_kde / 2., bin_size_kde) +
                             bin_size_kde / 2.),
                       density=True)
    return out, target_pLeft


def get_target_data_per_trial(
        target_times, target_data, interval_begs, interval_ends, binsize, allow_nans=False):
    """Select wheel data for specified interval on each trial.

    Parameters
    ----------
    target_times : array-like
        time in seconds for each sample
    target_data : array-like
        data samples
    interval_begs : array-like
        beginning of each interval in seconds
    interval_ends : array-like
        end of each interval in seconds
    binsize : float
        width of each bin in seconds
    allow_nans : bool, optional
        False to skip trials with >0 NaN values in target data

    Returns
    -------
    tuple
        - (list): time in seconds for each trial
        - (list): data for each trial

    """

    n_bins = int((interval_ends[0] - interval_begs[0]) / binsize) + 1
    idxs_beg = np.searchsorted(target_times, interval_begs, side='right')
    idxs_end = np.searchsorted(target_times, interval_ends, side='left')
    target_times_og_list = [target_times[ib:ie] for ib, ie in zip(idxs_beg, idxs_end)]
    target_data_og_list = [target_data[ib:ie] for ib, ie in zip(idxs_beg, idxs_end)]

    # interpolate and store
    target_times_list = []
    target_data_list = []
    good_trial = [None for _ in range(len(target_times_og_list))]
    for i, (target_time, target_vals) in enumerate(zip(target_times_og_list, target_data_og_list)):
        if len(target_vals) == 0:
            print('target data not present on trial %i; skipping' % i)
            good_trial[i] = False
            continue
        if np.sum(np.isnan(target_vals)) > 0 and not allow_nans:
            print('nans in target data on trial %i; skipping' % i)
            good_trial[i] = False
            continue
        if np.abs(interval_begs[i] - target_time[0]) > binsize:
            print('target data starts too late on trial %i; skipping' % i)
            good_trial[i] = False
            continue
        if np.abs(interval_ends[i] - target_time[-1]) > binsize:
            print('target data ends too early on trial %i; skipping' % i)
            good_trial[i] = False
            continue
        # x_interp = np.arange(target_time[0], target_time[-1] + binsize / 2, binsize)
        x_interp = np.linspace(target_time[0], target_time[-1], n_bins)
        if len(target_vals.shape) > 1 and target_vals.shape[1] > 1:
            n_dims = target_vals.shape[1]
            y_interp_tmps = []
            for n in range(n_dims):
                y_interp_tmps.append(scipy.interpolate.interp1d(
                    target_time, target_vals[:, n], kind='linear',
                    fill_value='extrapolate')(x_interp))
            y_interp = np.hstack([y[:, None] for y in y_interp_tmps])
        else:
            y_interp = scipy.interpolate.interp1d(
                target_time, target_vals, kind='linear', fill_value='extrapolate')(x_interp)
        target_times_list.append(x_interp)
        target_data_list.append(y_interp)
        good_trial[i] = True

    return target_times_list, target_data_list, np.array(good_trial)


def get_target_data_per_trial_error_check(
        target_times, target_vals, trials_df, align_event, align_interval, binsize):
    """High-level function to split target data over trials, with error checking.

    Parameters
    ----------
    target_times : array-like
        time in seconds for each sample
    target_vals : array-like
        data samples
    trials_df : pd.DataFrame
        requires a column that matches `align_event`
    align_event : str
        event to align interval to
        firstMovement_times | stimOn_times | feedback_times
    align_interval : tuple
        (align_begin, align_end); time in seconds relative to align_event
    binsize : float
        size of individual bins in interval

    Returns
    -------
    tuple
        - (list): time in seconds for each trial
        - (list): data for each trial
        - (array-like): mask of good trials (True) and bad trials (False)

    """

    align_times = trials_df[align_event].values
    interval_beg_times = align_times + align_interval[0]
    interval_end_times = align_times + align_interval[1]

    # split data by trial
    target_times_list, target_val_list, good_trials = get_target_data_per_trial(
        target_times, target_vals, interval_beg_times, interval_end_times, binsize)

    return target_times_list, target_val_list, good_trials
