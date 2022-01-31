#!/usr/bin/env python3

""" Script to decode image type (negative vs. neutral) from fmri brain activity """

import os
import pandas as pd
import numpy as np
from nilearn.image import mean_img
from nilearn.image import load_img, index_img
from nilearn.plotting import view_img, plot_roi, plot_stat_map
from nilearn.decoding import Decoder
from sklearn.model_selection import RepeatedKFold
from sklearn.model_selection import LeaveOneGroupOut

# define functions
def load_data(strategy: str, preprocessing: str, data_path: str, plot: bool=False):
    # get conditions data from txt file
    fname_onsets = data_path + 'Onsets/' + os.listdir(data_path + 'Onsets')[0]  # get filename of text file with onsets (output from opennft)
    onsets_all = pd.read_csv(fname_onsets, delimiter='\t', index_col=False, skiprows=4)  # load txt file with onsets
    onsets = onsets_all[onsets_all['strategie'] == strategy]
    cond_names = [' neutre', ' regulation']  # set names of two main conditions (like in txt file) ! pay attention to space bar before word (coming from text file)
    tr = 2  # set TR (in seconds)
    conds_fmri = get_conds_from_txt(onsets, cond_names, tr)  # convert onset data to conditions file (['block', 'condition', 'TR'] with one row per TR)
    conds_fmri = conds_fmri.iloc[list(range(0, 215))]  # todo: only to correct for final onset (and for debugging, to align phantom pilot onsets with pilot 2 data); ! remove later !
    #extra_lines = pd.DataFrame(index=["215", "216"], columns=["block", "condition", "TR"])  # quick fix if brain data and conds are misaligned; todo: change later!
    #conds_fmri = conds_fmri.append(extra_lines)  # quick fix if brain data and conds are misaligned; todo: change later!
    # load brain data
    fmri_data, fname_anat = load_brain_data(preprocessing, data_path)  # load fmri data and T1
    fmri_data = index_img(fmri_data, range(len(conds_fmri)))  # cut brain data to onsets (cut off last scale tr's that are variable and cannot be read from onsets.txt)
        # todo: change, instead stabilize onset - data alignment!
    # import mask (and binarize, if specified)
    fname_mask = os.listdir(data_path + 'Mask_ROI_emo')[0]  # get filename of first mask in folder
    mask = load_img(img=data_path + 'Mask_ROI_emo/' + fname_mask)  # load binarized mask
    # select only stimulus trials (in brain and behavioral data)
    conditions_all = conds_fmri['condition']
    condition_mask = conditions_all.isin(cond_names)  # index to restrict data to negative or neutral stimuli
    fmri_niimgs = index_img(fmri_data, condition_mask)  # select only neutral/negative trials from
    conditions_trials = conditions_all[condition_mask]
    conditions = conditions_trials.values  # Convert to numpy array
    # plot results for checking
    if plot:
        print(fmri_data.shape)  # print shape of fmri data
        p1 = view_img(mean_img(fmri_data), threshold=None)  # todo: sth. wrong with brain data?
        p1.open_in_browser()
        plot_roi(mask, bg_img=fname_anat, cmap='Paired')  # plot mask
    return fmri_niimgs, fname_anat, mask, conds_fmri, condition_mask, conditions, cond_names

def get_conds_from_txt(onsets, cond_names, tr):
    onsets_tr_a = onsets[['condition', 'onsets_seconds']]  # select condition and onsets from txt file
    # convert onsets (in seconds) to TR
    onsets_tr = onsets_tr_a.copy()  # copy data frame, due to pandas rules
    onsets_tr.loc[:, ('dur_TR', 'TR')] = np.nan
    for row, onset in enumerate(onsets_tr['onsets_seconds']):
        onsets_tr.at[row, 'dur_TR'] = int(round(onset / tr))  # convert seconds to TR
    n_TR = int(list(onsets_tr['dur_TR'])[-1])  # total number of TR
    # set up and fill final conditions file with n_rows = n_TR
    colnames = ['block', 'condition', 'TR']  # define variable names
    conds_fmri = pd.DataFrame(index=range(n_TR), columns=colnames)  # set up data frame
    conds_fmri['TR'] = range(n_TR)  # fill in one TR per row
    # set conditions
    for row, TR in enumerate(conds_fmri['TR']):
        # find (closest previous) condition for each TR
        diff_to_tr = np.array([TR - dur_TR for dur_TR in onsets_tr['dur_TR']])  # compare TR of each row to TRs in original table
        diff_to_tr_pos = np.where(diff_to_tr > 0, diff_to_tr, np.inf)  # set negative values to infinity
        i_closest = diff_to_tr_pos.argmin()  # find condition of closest positive diff.
        conds_fmri.at[row, 'condition'] = onsets_tr['condition'][i_closest]  # set closest condition
    # set block number from condition
    i_block = 0
    for row, cond in enumerate(conds_fmri['condition']):
        conds_fmri.at[row, 'block'] = i_block  # set block number
        # find last trial of each block
        if row < (n_TR - 1):  # for every trial except very last trial
            cond_next_trial = conds_fmri['condition'][row + 1]  # get condition of subsequent trial
            if cond == cond_names[1] and cond_next_trial != cond_names[
                1]:  # if condition equals 'regulation' and subsequent trial does not (-> final regulation trial)
                i_block = i_block + 1  # set block counter + 1
    return conds_fmri

def load_brain_data(preprocessing, data_path):
    # enter preprocessing arg as 'r', 'sr', or 'swr
    # import fmri data
    fnames_fmri = os.listdir(data_path + 'EPIs_baseline')
    fname_fmri = [item for item in fnames_fmri if item.startswith(preprocessing)][0]
    fmri_data = load_img(data_path + 'EPIs_baseline/' + fname_fmri)  # concatenate brain data
    # import anatomical data (T1)
    fnames_anat = os.listdir(data_path + 'T1')
    fname_anat = data_path + 'T1/' + [item for item in fnames_anat if item.startswith('2') and item.endswith('.nii')][0]
    return fmri_data, fname_anat

def perform_decoding_cv(conditions, fmri_niimgs, mask, conds_fmri, condition_mask, random_state: int, cv_type: str, n_folds: int, anova: bool):
    # perform feature reduction via anova
    if anova:
        smoothing_fwhm = 8
        screening_percentile = 5
    else:
        smoothing_fwhm = None
        screening_percentile = 20
    # determine cv method
    if cv_type == 'k_fold':
        cv = RepeatedKFold(n_splits=n_folds, n_repeats=5, random_state=random_state)  # todo: add n_repeats as input option?
        scoring = 'accuracy'
        groups = None
    elif cv_type == 'block_out':
        cv = LeaveOneGroupOut()
        scoring = 'roc_auc'  # todo: discuss/check
        groups = conds_fmri[condition_mask]['block']
    else:
        print('Input error "cv_type": Please indicate either as "k_fold" or as "block_out"')
        return
    # build decoder
    decoder = Decoder(estimator='svc', mask=mask, cv=cv, screening_percentile=screening_percentile,
                      scoring=scoring, smoothing_fwhm=smoothing_fwhm, standardize=True)  # todo: discuss settings with Pauline
    # fit decoder
    decoder.fit(fmri_niimgs, conditions, groups=groups)
    return decoder

def plot_weights(decoder, fname_anat, condition):
    # plot model weights
    #coef_ = decoder.coef_
    #print(coef_.shape)
    weigth_img = decoder.coef_img_[condition]
    plot_stat_map(weigth_img, bg_img=fname_anat, title='SVM weights')
    #p2 = view_img(weigth_img, bg_img=fname_anat, title="SVM weights", dim=-1)  # todo: seems to select false T1?
    #p2.open_in_browser()
    return

def save_accs_to_txt(mean_score, scores, data_path):
    with open(data_path + 'W1/decoding_accuracies.txt', 'w') as f:
        f.write('mean accuracy across folds:')
        f.write('\n')
        f.write(str(mean_score))
        f.write('\n')
        f.write('accuracies per cv fold and repetition:')
        f.write('\n')
        f.write(str(scores))
    return


# define main params
data_path = "C:/Users/pp262170/Documents/NF_BD/Pilot_study/Pilot_20210727/SESSION_1/"  # set path to data folder of current set
preprocessing = "sr"  # specify as 'r' (realigned), 'sr' (realigned + smoothed), or 'swr' (sr + normalization); if swr, set perform_decoding_cv(anova=True)
cv_type = 'k_fold'  # cross-validation type: either 'k_fold' or 'block_out'
n_folds = 10  # number of folds to perform in k-fold cross-validation; only used if cv_type == 'k_fold'
anova = False  # if True, anova is performed as feature reduction method prior to decoding
strategy = "Pas d'instructions"  # specify strategy to decode, corresponding to the brain data in the folder (from "Affects positifs", "Pleine conscience", "Reevaluation cognitive", "Pas d'instructions")
random_state = 42

# load data
fmri_niimgs, fname_anat, mask, conds_fmri, condition_mask, conditions, cond_names = \
    load_data(strategy, preprocessing, data_path, plot=False)

# build and fit decoder in cv
decoder = perform_decoding_cv(conditions, fmri_niimgs, mask, conds_fmri,
                              condition_mask, random_state, cv_type=cv_type, n_folds=n_folds, anova=anova)

# plot decoder weights
plot_weights(decoder, fname_anat, condition=cond_names[1])

# save decoder weights
weigth_img = decoder.coef_img_[cond_names[1]]
weigth_img.to_filename(data_path + 'W1/weights.nii')

# evaluate decoder
scores = decoder.cv_scores_[cond_names[1]]  # classification accuracy for each fold
mean_score = np.mean(scores)  # average classification accuracy across folds
# save evaluation results in txt file
save_accs_to_txt(mean_score, scores, data_path)
#print(mean_score)
#print(np.std(scores))

# plot_stat_map(weigth_img, bg_img=T1, title="SVM weights")
# p2 = plotting.view_img(weigth_img, bg_img=T1, title="SVM weights", dim=-1)
# p2.open_in_browser()
