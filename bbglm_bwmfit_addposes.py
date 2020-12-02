"""
Script to use new neuralGLM object from brainbox rather than complicated matlab calls

Berk, May 2020
"""

from oneibl import one
import numpy as np
import pandas as pd
from brainbox.modeling import glm
import brainbox.io.one as bbone
from export_funs_addposes import trialinfo_to_df
from prior_funcs import fit_sess_psytrack
from datetime import date
import pickle
import os

offline = True
one = one.ONE()

ephys_cache = {}


def fit_session(session_id, kernlen, nbases,
                t_before=1., t_after=0.6, prior_estimate='psytrack', max_len=2., probe_idx=0,
                method='minimize', alpha=0, contnorm=5., binwidth=0.02, wholetrial_step=False,
                blocktrain=False, abswheel=False, abs_poses_vel=False):
    if not abswheel:
        signwheel = True
    else:
        signwheel = False
    trialsdf = trialinfo_to_df(session_id, maxlen=max_len, t_before=t_before, t_after=t_after,
                               glm_binsize=binwidth, ret_abswheel=abswheel, ret_wheel=signwheel, 
                               abs_poses_vel=abs_poses_vel)
    if prior_estimate == 'psytrack':
        print('Fitting psytrack esimates...')
        wts, stds = fit_sess_psytrack(session_id, maxlength=max_len, as_df=True)
        wts['bias'] = wts['bias'] - np.mean(wts['bias'])
        fitinfo = pd.concat((trialsdf, wts['bias']), axis=1)
        bias_next = np.roll(fitinfo['bias'], -1)
        bias_next = pd.Series(bias_next, index=fitinfo['bias'].index)[:-1]
        fitinfo['bias_next'] = bias_next
    elif prior_estimate is None:
        fitinfo = trialsdf.copy()
    else:
        raise NotImplementedError('Only psytrack currently available')
    # spk_times = one.load(session_id, dataset_types=['spikes.times'], offline=offline)[probe_idx]
    # spk_clu = one.load(session_id, dataset_types=['spikes.clusters'], offline=offline)[probe_idx]

    # A bit of messy loading to get spike times, clusters, and cluster brain regions.
    # This is the way it is because loading with regions takes forever. The weird for loop
    # ensures that we don't waste memory storing unnecessary and large arrays.
    probestr = 'probe0' + str(probe_idx)
    if session_id not in ephys_cache:
        spikes, clusters, _ = bbone.load_spike_sorting_with_channel(session_id, one=one,
                                                                    aligned=True)
        for probe in spikes:
            null_keys_spk = []
            for key in spikes[probe]:
                if key not in ('times', 'clusters'):
                    null_keys_spk.append(key)
            for key in null_keys_spk:
                _ = spikes[probe].pop(key)
            null_keys_clu = []
            for key in clusters[probe]:
                if key not in ('acronym', 'atlas_id'):
                    null_keys_clu.append(key)
            for key in null_keys_clu:
                _ = clusters[probe].pop(key)
        ephys_cache[session_id] = (spikes, clusters)
    else:
        spikes, clusters = ephys_cache[session_id]
    spk_times = spikes[probestr].times
    spk_clu = spikes[probestr].clusters
    clu_regions = clusters[probestr].acronym
    fitinfo['pLeft_last'] = pd.Series(np.roll(fitinfo['probabilityLeft'], 1),
                                      index=fitinfo.index)[:-1]
    fitinfo = fitinfo.iloc[1:-1]
    fitinfo['adj_contrastLeft'] = np.tanh(contnorm * fitinfo['contrastLeft']) / np.tanh(contnorm)
    fitinfo['adj_contrastRight'] = np.tanh(contnorm * fitinfo['contrastRight']) / np.tanh(contnorm)
    # For 3D points
    # pose_covars = ['paw1_x', 'paw1_x_velocity', 
    #                'paw1_y', 'paw1_y_velocity',
    #                'paw1_z', 'paw1_z_velocity',
    #                'paw2_x', 'paw2_x_velocity', 
    #                'paw2_y', 'paw2_y_velocity',
    #                'paw2_z', 'paw2_z_velocity',
    #                'nose_x', 'nose_x_velocity', 
    #                'nose_y', 'nose_y_velocity',
    #                'nose_z', 'nose_z_velocity',
    #                #'pupil_diameter_r', 'pupil_diameter_r_velocity',
    #                #'pupil_diameter_l', 'pupil_diameter_l_velocity',
    #                'lick_timing']
    
    # For 2D points
    pose_covars = ['paw1_x_velocity', 'paw1_y_velocity', 
                   'paw2_x_velocity','paw2_y_velocity', 'lick_timing']
    # Drop NaN rows
    fitinfo = fitinfo.loc[fitinfo[pose_covars].dropna().index]
    vartypes = {'choice': 'value',
                'response_times': 'timing',
                'probabilityLeft': 'value',
                'pLeft_last': 'value',
                'feedbackType': 'value',
                'feedback_times': 'timing',
                'contrastLeft': 'value',
                'adj_contrastLeft': 'value',
                'contrastRight': 'value',
                'adj_contrastRight': 'value',
                'goCue_times': 'timing',
                'stimOn_times': 'timing',
                'trial_start': 'timing',
                'trial_end': 'timing',
                'bias': 'value',
                'bias_next': 'value',
                'wheel_velocity': 'continuous'}
    for pose_covar in pose_covars:
        vartypes[pose_covar] = 'continuous'
        # Normalize velocity into range -1~1 (0~1 if absolut velocity applied)
        if pose_covar != 'lick_timing':
            max_value = np.max(np.hstack([np.max(np.abs(trial)) 
                                          for trial in fitinfo[pose_covar].values]))
            fitinfo[pose_covar] /= max_value
    nglm = glm.NeuralGLM(fitinfo, spk_times, spk_clu, vartypes, binwidth=binwidth, subset=True,
                         blocktrain=blocktrain)
    nglm.clu_regions = clu_regions

    if t_before < 0.7:
        raise ValueError('t_before needs to be 0.7 or greater in order to do -0.1 to -0.7 step'
                         ' function on pLeft')

    stepbounds = [nglm.binf(t_before - 0.7), nglm.binf(t_before - 0.1)]

    def stepfunc(row):
        currvec = np.ones(nglm.binf(row.feedback_times)) * row.pLeft_last
        nextvec = np.ones(nglm.binf(row.duration) - nglm.binf(row.feedback_times)) *\
            row.probabilityLeft
        return np.hstack((currvec, nextvec))

    def stepfunc_prestim(row):
        stepvec = np.zeros(nglm.binf(row.duration))
        stepvec[stepbounds[0]:stepbounds[1]] = row.pLeft_last
        return stepvec

    def stepfunc_bias(row):
        currvec = np.ones(nglm.binf(row.feedback_times)) * row.bias
        nextvec = np.ones(nglm.binf(row.duration) - nglm.binf(row.feedback_times)) *\
            row.bias_next
        return np.hstack((currvec, nextvec))

    cosbases_long = glm.full_rcos(kernlen, nbases, nglm.binf)
    cosbases_short = glm.full_rcos(0.4, nbases, nglm.binf)
    nglm.add_covariate_timing('stimonL', 'stimOn_times', cosbases_long,
                              cond=lambda tr: np.isfinite(tr.contrastLeft),
                              deltaval='adj_contrastLeft',
                              desc='Kernel conditioned on L stimulus onset')
    nglm.add_covariate_timing('stimonR', 'stimOn_times', cosbases_long,
                              cond=lambda tr: np.isfinite(tr.contrastRight),
                              deltaval='adj_contrastRight',
                              desc='Kernel conditioned on R stimulus onset')
    nglm.add_covariate_timing('correct', 'feedback_times', cosbases_long,
                              cond=lambda tr: tr.feedbackType == 1,
                              desc='Kernel conditioned on correct feedback')
    nglm.add_covariate_timing('incorrect', 'feedback_times', cosbases_long,
                              cond=lambda tr: tr.feedbackType == -1,
                              desc='Kernel conditioned on incorrect feedback')
    if prior_estimate is None and wholetrial_step:
        nglm.add_covariate_raw('pLeft', stepfunc, desc='Step function on prior estimate')
    elif prior_estimate is None and not wholetrial_step:
        nglm.add_covariate_raw('pLeft', stepfunc_prestim, desc='Step function on prior estimate')
    elif prior_estimate == 'psytrack':
        nglm.add_covariate_raw('pLeft', stepfunc_bias, desc='Step function on prior estimate')
    # nglm.add_covariate('wheel', fitinfo['wheel_velocity'], cosbases_short, -0.4)
    for pose_covar in pose_covars:
        nglm.add_covariate(pose_covar, fitinfo[pose_covar], cosbases_short, -0.4)
    nglm.compile_design_matrix()
    nglm.fit(method=method, alpha=alpha)
    combined_weights = nglm.combine_weights()
    return nglm, combined_weights


if __name__ == "__main__":
    from ibl_pipeline import subject, ephys, histology
    from ibl_pipeline.analyses import behavior as behavior_ana
    from glob import glob
    import traceback
    # currdate = str(date.today())
    currdate = '2020-11-17'
    regionlabeled = histology.ProbeTrajectory &\
        'insertion_data_source = "Ephys aligned histology track"'
    sessions = subject.Subject * subject.SubjectProject * ephys.acquisition.Session *\
        regionlabeled * behavior_ana.SessionTrainingStatus
    bwm_sess = sessions & 'subject_project = "ibl_neuropixel_brainwide_01"' &\
        'good_enough_for_brainwide_map = 1'
    sessinfo = [info for info in bwm_sess]
    kernlen = 0.6
    nbases = 10
    alpha = 0
    method = 'pytorch'
    blocking = True
    prior_estimate = None
    binwidth = 0.02

    for s in sessinfo:
        # Check if DLC outputs are avaliable
        sessid = str(s['session_uuid'])
        dtypes = one.list(sessid, 'dataset-types')
        dataset_types = ['camera.times',
                         'trials.intervals',
                         'camera.dlc']
        if not all([i in dtypes for i in dataset_types]):
            #sessinfo.remove(s)
            continue
        nickname = s['subject_nickname']
        sessdate = str(s['session_start_time'].date())
        probe = s['probe_idx']
        print(f'\nWorking on {nickname} from {sessdate}\n')
        filename = '/media/berk/Storage1/fits/' +\
            f'{nickname}/{sessdate}_session_{currdate}_probe{probe}_fit.p'
        if not os.path.exists(f'/media/berk/Storage1/fits/{nickname}'):
            os.mkdir(f'/media/berk/Storage1/fits/{nickname}')
        subpaths = [n for n in glob(os.path.abspath(filename)) if os.path.isfile(n)]
        if len(subpaths) != 0:
            print(f'Skipped {nickname}, {sessdate}, probe{probe}: already fit.')
        if len(subpaths) == 0:
            if not offline:
                _ = one.load(sessid, download_only=True)
            try:
                nglm, sessweights = fit_session(sessid, kernlen, nbases,
                                                prior_estimate=prior_estimate,
                                                probe_idx=probe, method=method, alpha=alpha,
                                                binwidth=binwidth, blocktrain=blocking)
            except Exception as err:
                tb = traceback.format_exc()
                print(f'Subject {nickname} on {sessdate} failed:\n', type(err), err)
                print(tb)
                continue

            outdict = {'sessinfo': s, 'kernlen': kernlen, 'nbases': nbases, 'weights': sessweights,
                       'fitobj': nglm}
            today = str(date.today())
            subjfilepath = os.path.abspath(filename)
            fw = open(subjfilepath, 'wb')
            pickle.dump(outdict, fw)
            fw.close()
