#!/usr/bin/env python

import os
import re
import pickle
import numpy as np
import plotly.plotly as py
import plotly.graph_objs as go
from pyannote.core import Annotation, Segment
from sklearn.cluster import KMeans as sklearnKMeans
from pyannote.metrics.diarization import DiarizationErrorRate

from tools import Tools
from kmeans import KMeans
from normalization import Normalization
from tools import loginfo, logwarning
from user_exception import DiarizationException


class Diarization(Normalization):
    """ Diarization class used as main diarization focused implementation.

    """
    def __init__(self, input_list, norm_list, ivecs_dir, out_dir, plda_model_dir):
        """ Class constructor.

            :param input_list: path to lisst of input files
            :type input_list: str
            :param norm_list: path to list of normalization files
            :type norm_list: str
            :param ivecs_dir: path to directory containing i-vectors
            :type ivecs_dir: str
            :param out_dir: path to output directory
            :type out_dir: str
            :param plda_model_dir: path to models directory
            :type plda_model_dir: str
        """
        super(Diarization, self).__init__(ivecs_dir, norm_list, plda_model_dir)
        self.input_list = input_list
        self.ivecs_dir = ivecs_dir
        self.out_dir = out_dir
        self.ivecs = list(self.load_ivecs())

    def get_ivec(self, name):
        """ Get i-ivector set by name.

            :param name: name of the set
            :type name: str
            :returns: set of i-vectors
            :rtype: IvecSet
        """
        for ii in self.ivecs:
            print ii.name
            if name == ii.name:
                return ii
        raise DiarizationException(
            '[Diarization.get_ivec] Name of the set not found - {}.'.format(name)
        )

    def load_ivecs(self):
        """ Load i-vectors stored as pickle files.

            :returns: list of i-vectors sets
            :rtype: list
        """
        with open(self.input_list, 'r') as f:
            for line in f:
                loginfo('[Diarization.load_ivecs] Loading pickle file {} ...'.format(line.rstrip().split()[0]))
                line = line.rstrip()
                try:
                    if len(line.split()) == 1:
                        with open(os.path.join(self.ivecs_dir, line + '.pkl')) as i:
                            yield pickle.load(i)
                    elif len(line.split()) == 2:
                        file_name = line.split()[0]
                        num_spks = int(line.split()[1])
                        with open(os.path.join(self.ivecs_dir, file_name + '.pkl')) as i:
                            ivec_set = pickle.load(i)
                            ivec_set.num_speakers = num_spks
                            yield ivec_set
                    else:
                        raise DiarizationException(
                            '[Diarization.load_ivecs] Unexpected number of columns in input list {}.'.format(
                                self.input_list)
                        )
                except IOError:
                    logwarning(
                        '[Diarization.load_ivecs] No pickle file found for {}.'.format(line.rstrip().split()[0]))

    def score(self):
        """ Score i-vectors agains speaker clusters.

            :returns: PLDA scores
            :rtype: numpy.array
        """
        scores_dict = {}
        for ivecset in self.ivecs:
            name = os.path.normpath(ivecset.name)
            ivecs = ivecset.get_all()
            loginfo('[Diarization.score] Scoring {} ...'.format(name))
            size = ivecset.size()
            if size > 0:
                if ivecset.num_speakers is not None:
                    num_speakers = ivecset.num_speakers
                    sklearnkmeans = sklearnKMeans(n_clusters=num_speakers).fit(ivecs)
                    centroids = KMeans(sklearnkmeans.cluster_centers_, num_speakers, self.plda).fit(ivecs)
                else:
                    if self.norm_ivecs is not None:
                        num_speakers, centroids = self.get_num_speakers(ivecs)
                    else:
                        raise DiarizationException(
                            '[Diarization.score] Can not estimate number of speakers without training set.'
                        )
                if self.norm_list is None:
                    scores_dict[name] = self.plda.score(ivecs, centroids, self.scale, self.shift)
                else:
                    scores_dict[name] = self.s_norm(ivecs, centroids)
            else:
                logwarning('[Diarization.score] No i-vectors to score in {}.'.format(ivecset.name))
        return scores_dict

    def dump_rttm(self, scores):
        """ Dump rttm files to disk.

            :param scores: input scores from PLDA model
            :type scores: numpy.array
        """
        for ivecset in self.ivecs:
            if ivecset.size() > 0:
                name = ivecset.name
                # dirty trick, will be removed, watch out
                if 'beamformed' in ivecset.name:
                    ivecset.name = re.sub('beamformed/', '', ivecset.name)
                # # # # # # # # # # # # # # # # # # # # #
                reg_name = re.sub('/.*', '', ivecset.name)
                Tools.mkdir_p(os.path.join(self.out_dir, os.path.dirname(name)))
                with open(os.path.join(self.out_dir, name + '.rttm'), 'w') as f:
                    for i, ivec in enumerate(ivecset.ivecs):
                        start, end = ivec.window_start, ivec.window_end
                        idx = np.argmax(scores[name].T[i])
                        f.write('SPEAKER {} 1 {} {} <NA> <NA> {}_spkr_{} <NA>\n'.format(
                            reg_name, float(start / 1000.0), float((end - start) / 1000.0), reg_name, idx))
            else:
                logwarning('[Diarization.dump_rttm] No i-vectors to dump in {}.'.format(ivecset.name))

    def get_der(self, ref_file, scores):
        """ Compute Diarization Error Rate from reference and scores.

            :param ref_file: path to file with diarization reference
            :type ref_file: str
            :param scores: input scores from PLDA model
            :type scores: numpy.array
        """
        ref, hyp = self.init_annotations()
        with open(ref_file, 'r') as f:
            for line in f:
                _, name, _, start, duration, _, _, speaker, _ = line.split()
                ref[name][Segment(float(start), float(start) + float(duration))] = speaker
        for ivecset in self.ivecs:
            if ivecset.size() > 0:
                name, reg_name = ivecset.name, ivecset.name
                # dirty trick, will be removed, watch out
                if 'beamformed' in name:
                    reg_name = re.sub('beamformed/', '', name)
                # # # # # # # # # # # # # # # # # # # # #
                reg_name = re.sub('/.*', '', reg_name)
                for i, ivec in enumerate(ivecset.ivecs):
                    start, end = ivec.window_start / 1000.0, ivec.window_end / 1000.0
                    hyp[reg_name][Segment(start, end)] = np.argmax(scores[name].T[i])
            else:
                logwarning('[Diarization.get_der] No i-vectors to dump in {}.'.format(ivecset.name))
        der = DiarizationErrorRate()
        der.collar = 0.25
        names, values, summ = [], [], 0.0
        for name in ref.keys():
            names.append(name)
            der_num = der(ref[name], hyp[name]) * 100
            values.append(der_num)
            summ += der_num
            loginfo('[Diarization.get_der] {} DER = {}'.format(name, '{0:.3f}'.format(der_num)))
        loginfo('[Diarization.get_der] Average DER = {}'.format('{0:.3f}'.format(summ / float(len(ref.keys())))))
        Diarization.plot_der(names, values)

    def init_annotations(self):
        """ Initialize hypothesis and reference annotations dictionary.

            :returns: initialized reference and hypothesis dictionary
            :rtype: tuple
        """
        ref, hyp = {}, {}
        for ivecset in self.ivecs:
            if ivecset.size() > 0:
                name = ivecset.name
                # dirty trick, will be removed, watch out
                if 'beamformed' in name:
                    name = re.sub('beamformed/', '', name)
                # # # # # # # # # # # # # # # # # # # # #
                name = re.sub('/.*', '', name)
                ref[name], hyp[name] = Annotation(), Annotation()
        return ref, hyp

    def get_num_speakers(self, ivecs, min_speakers=2, max_speakers=6):
        """ Obtain number of speakers from pretrained model.

            :param ivecs: input i-vectors
            :type ivecs: numpy.array
            :param min_speakers: minimal number of speakers from model
            :type min_speakers: int
            :param max_speakers: maximal number of speakers from model
            :type max_speakers: int
            :returns: estimated number of speakers and KMeans centroid
            :rtype: tuple
        """
        avg, centroids_list = [], []
        features = []
        for num_speakers in range(min_speakers, max_speakers + 1):
            sklearnkmeans = sklearnKMeans(n_clusters=num_speakers).fit(ivecs)
            centroids = KMeans(sklearnkmeans.cluster_centers_, num_speakers, self.plda).fit(ivecs)
            centroids_list.append(centroids)
            scores = self.s_norm(centroids, centroids)[np.tril_indices(num_speakers, -1)]
            features.append(Normalization.get_features(scores))
        num_speakers = np.argmax(np.sum(self.model.test(features, prob=True), axis=0))
        # raw_input('ENTER')
        return num_speakers + min_speakers, centroids_list[num_speakers]

    @staticmethod
    def plot_der(names, values):
        """ Plot DER per file using plotly and upload it to server.

            :param names: names of files
            :type names: list
            :param values: according values for files
            :type values: list
        """
        values = [x for (y, x) in sorted(zip(names, values))]
        names = sorted(names)
        print values, names
        trace1 = go.Scatter(x=names, y=values)
        layout = dict(title='Diarization Error Rate - Beamformed',
                      xaxis=dict(title='Recording'),
                      yaxis=dict(title='DER [%]'),
                      )
        fig = dict(data=[trace1], layout=layout)
        py.iplot(fig, filename='Diarization Error Rate')
