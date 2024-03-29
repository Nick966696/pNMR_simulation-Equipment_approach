# -*- coding: utf-8 -*-
import numpy as np
from scipy.optimize import minimize
from scipy.stats import linregress
from scipy.ndimage.filters import uniform_filter1d
from scipy.fftpack import fft, ifft, fftfreq
from ..units import *
from .hilbert_transform import HilbertTransform
import copy
import json
import matplotlib.pyplot as plt
from ROOT import TMatrixD

def MatrixInvertRoot(M, tol=1e-32):
    M_root = TMatrixD(np.shape(M)[0], np.shape(M)[1])
    for i in range(np.shape(M)[0]):
        for j in range(np.shape(M)[1]):
            M_root[i][j] = M[i,j]
    M_root.SetTol(tol)
    M_root.Invert()
    M_inv = np.zeros_like(M)
    for i in range(np.shape(M)[0]):
        for j in range(np.shape(M)[1]):
            M_inv[i,j] = M_root[i][j]
    return M_inv

class PhaseFitFID(object):
    fit_version = {"t3_odd": {"nParams": 3, "func": lambda t, p: p[0] + p[1]*t             + p[2]*t**3},
                   "t5_odd": {"nParams": 4, "func": lambda t, p: p[0] + p[1]*t             + p[2]*t**3             + p[3]*t**5},
                   "t7_odd": {"nParams": 5, "func": lambda t, p: p[0] + p[1]*t             + p[2]*t**3             + p[3]*t**5             + p[4]*t**7},
                   "t3_all": {"nParams": 4, "func": lambda t, p: p[0] + p[1]*t + p[2]*t**2 + p[3]*t**3},
                   "t4_all": {"nParams": 5, "func": lambda t, p: p[0] + p[1]*t + p[2]*t**2 + p[3]*t**3 + p[4]*t**4},
                   "t5_all": {"nParams": 6, "func": lambda t, p: p[0] + p[1]*t + p[2]*t**2 + p[3]*t**3 + p[4]*t**4 + p[5]*t**5},
                   "t6_all": {"nParams": 7, "func": lambda t, p: p[0] + p[1]*t + p[2]*t**2 + p[3]*t**3 + p[4]*t**4 + p[5]*t**5 + p[6]*t**6},
                   "t7_all": {"nParams": 8, "func": lambda t, p: p[0] + p[1]*t + p[2]*t**2 + p[3]*t**3 + p[4]*t**4 + p[5]*t**5 + p[6]*t**6 + p[7]*t**7},
                   }

    def __init__(self, probe=None, edge_ignore=0.1*ms, frac=np.exp(-1), smoothing=True, tol=1e-5, n_smooth=3, phase_template_file=None, fit_range_template_file=None, fit_mode="t5_odd"):
        self.t0 = probe.time_pretrigger
        self.pretrigger = probe.time_pretrigger
        self.readout_length = probe.readout_length
        self.edge_ignore = edge_ignore
        self.frac = frac
        self.smoothing = smoothing
        self.tol = tol
        self.n_smooth = n_smooth
        self.nParams = self.fit_version[fit_mode]["nParams"]
        self.fit_func = self.fit_version[fit_mode]["func"]
        if phase_template_file is not None:
            self.load_phase_template(phase_template_file)
        if fit_range_template_file is not None:
            self.load_fit_range_template(fit_range_template_file)

    def load_phase_template(self, path):
        if path.endswith(".root"):
            import ROOT
            file = ROOT.TFile.Open(path,"READ")
            self.phase_template = np.reshape(file.Get("PhaseTemplate"), (-1, 4096))
        else:
            self.phase_template = np.genfromtxt(path, delimiter=",")

    def load_fit_range_template(self, path):
        with open(path, "r") as open_file:
            raw_data = json.load(open_file)
        self.fit_range_template = {entry["Probe ID"]: (entry["Fid Begin"], entry["Fid End"]) for entry in raw_data}

    def get_fit_range(self):
        t_min = np.min(self.time)
        t_max = np.max(self.time)
        if self.pretrigger is not None:
            t_min = np.max([t_min, self.pretrigger])
        if self.readout_length is not None:
            t_max = np.min([t_max, self.readout_length])
        mask_edge = np.logical_and(self.time > t_min + self.edge_ignore,
                                   self.time < t_max - self.edge_ignore)

        thres = np.max(self.env[mask_edge])*self.frac
        mask = np.logical_and(mask_edge, self.env > thres)

        t_min = np.min(self.time[mask])
        t_max = np.min(self.time[np.logical_and(self.time > t_min, np.logical_not(mask))])
        return np.array([t_min, t_max])

    def apply_smoothing(self):
        N = int(self.window_size/np.diff(self.time)[0])
        if N%2 == 0:
            N += 1
        return uniform_filter1d(self.phase_raw, size=N)

    def get_noise(self):
        return np.std(self.flux[self.time < self.pretrigger])

    def chi2_fit(self):
        mask = np.logical_and(self.time > np.min(self.t_range), self.time < np.max(self.t_range))
        self.width = (self.t_range[1]-self.t_range[0])
        chi2 = lambda p: np.sum((self.fit_func((self.time[mask]-self.t0)/self.width, p) - self.phase[mask])**2*(self.env[mask]/self.noise)**2)
        x0 = np.random.normal(scale=0.1, size=self.nParams)
        x0[0] = self.offset_estimate*(1+x0[0])
        x0[1] = self.f_estimate*self.width*(1+x0[1])
        res = minimize(chi2, x0, tol=self.tol, method="L-BFGS-B")
        return res

    def fit(self, time, flux, probe_id=0):
        self.time = time
        self.flux = flux
        hilbert = HilbertTransform(self.time, self.flux)
        _, self.env =  hilbert.EnvelopeFunction()
        _, self.phase_raw =  hilbert.PhaseFunction()
        self.noise = self.get_noise()
        self.t_range = self.get_fit_range()
        mask = np.logical_and(self.t_range[0] < self.time, self.time < self.t_range[1])
        self.f_estimate, self.offset_estimate, _, _, _ = linregress(self.time[mask]-self.t0, self.phase_raw[mask])
        if self.smoothing:
            self.window_size = self.n_smooth*2*np.pi/self.f_estimate
            self.phase = self.apply_smoothing()
        else:
            self.phase = self.phase_raw[:]

        if hasattr(self, "phase_template"):
            self.phase -= self.phase_template[probe_id]

        self.res = self.chi2_fit()
        self.n_point_in_fit = np.sum(np.logical_and(self.time > np.min(self.t_range), self.time < np.max(self.t_range)))
        self.frequency = self.res.x[1]/self.width
        self.phi0 = self.res.x[0]

        return self.frequency

    def plot(self):
        plt.plot(self.time/ms, self.phase_raw - self.phi0 - self.frequency*(self.time-self.t0), color="b", label="raw FID")
        if self.smoothing:
            plt.plot(self.time/ms, self.phase - self.phi0 - self.frequency*(self.time-self.t0), color="red", label="smoothed FID")
            #plt.errorbar(self.time/ms, self.phase - self.phi0 - self.frequency*(self.time-self.t0), yerr=self.noise/self.env, color="red", label="smoothed FID")
        #plt.plot(time[mask]/ms, phi_fit[mask] - phi0 - frequency*(time[mask]-t0), color="k", ls="--", label="fit")
        phase_fit = self.fit_func((self.time-self.t0)/self.width, self.res.x)
        plt.plot(self.time/ms, phase_fit - self.phi0 - self.frequency*(self.time-self.t0), color="k", ls="--", label="fit")
        plt.grid()
        plt.axvspan(*(self.t_range/ms), color="gray", alpha=0.2)
        plt.ylim(-0.1, 0.2)
        plt.xlabel("time / ms")
        plt.xlim( (np.mean(self.t_range)-0.55*(self.t_range[1]-self.t_range[0]))/ms, (np.mean(self.t_range)+0.55*(self.t_range[1]-self.t_range[0]))/ms)
        plt.ylabel(r"$ \Phi(t) - \hat{\Phi_0} - \hat{\frac{\mathrm{d}\Phi}{\mathrm{d}t}}\cdot t$")
        plt.legend()
        plt.axvline(self.t0/ms, ls="--", color="k")
        plt.text(self.t0/ms, 0.20, "trigger", rotation=90, va="top", ha="left", fontsize=12, fontweight='bold')
        plt.xlim(xmin=self.t0/ms*0.95)

class PhaseFitRan(object):
    def __init__(self, t0=-420*us, baseline_start=0, baseline_end=400, smooth_iterations=2, LengthReduction=0.4, phase_template_path=None, fit_range_template_path=None):
        self.t0 = t0
        self.baseline_start = baseline_start
        self.baseline_end = baseline_end
        self.smooth_iterations = smooth_iterations
        self.LengthReduction = LengthReduction
        self.edge_ignore = 60*us
        self.start_amplitude = 0.37
        self.use_phase_template = phase_template_path is not None
        if self.use_phase_template:
            self.load_phase_template(phase_template_path)
        self.use_fit_range_template = fit_range_template_path is not None
        if self.use_fit_range_template:
            self.load_fit_range_template(fit_range_template_path)

    def load_phase_template(self, path):
        from ROOT import TTree, TFile, gROOT, AddressOf
        # struct defined as in here: https://cdcvs.fnal.gov/redmine/projects/gm2field/repository/revisions/develop/entry/include/RootTreeStructs.hh#L657
        gROOT.ProcessLine("""struct fidSettings_t {
            Double_t const_baseline;
            Double_t const_baseline_used;
            Double_t edge_width;
            Double_t edge_ignore;
            Double_t start_amplitude;
            Double_t baseline_freq_thresh;
            Double_t filter_low_freq;
            Double_t filter_high_freq;
            Double_t filter_freq_width;
            Double_t fft_peak_width;
            Double_t centroid_thresh;
            Double_t hyst_thresh;
            Double_t snr_thresh;
            Double_t len_thresh;
            Double_t t0_shift;
            Double_t t0_shift_corr;
            Double_t LengthReduction;
            Double_t LengthReduction1;
            Double_t LengthReduction2;
            Double_t LengthReduction3;
            Double_t SpikeThreshold;
            Double_t FreqTemplate[378];
            Double_t PhaseTemplate[378*4096];
            Int_t    PhaseTemplateN;
            Int_t fit_range_scheme;
            Int_t phase_fit_scheme;
            Int_t SmoothWidth;
            UInt_t TruncateBeginning;
            UInt_t TruncateEnd;
            UInt_t ZeroPadding;
            UInt_t const_baseline_start;
            UInt_t const_baseline_end;
            UInt_t baseline_mode;
            UInt_t baseline_event;
            UInt_t SmoothIteration;
            UInt_t poln;
            UInt_t auto_filter_window;
            UInt_t higher_order_correction;
            UInt_t ha_npar;
            UInt_t NSample;
            UInt_t CompareDistance;
            UInt_t HalfVetoWindow;
            UInt_t FitStart[378];
            UInt_t FitEnd[378];
            UInt_t NZeros[378];
            char filter[64];
            char PhaseTemplateFile[128];
            char TemplatePath[128];
            char FitRangeTemplateFile[128];}""")
        from ROOT import fidSettings_t
        data = fidSettings_t()
        f = TFile(path)
        tree = f.Get("SettingsCollector/settings")
        tree.SetBranchAddress("FixedProbeFid", AddressOf(data,"const_baseline"))
        tree.GetEntry(0)
        self.phase_template = np.array(np.frombuffer(data.PhaseTemplate, dtype='double').reshape([378,4096]))
        self.frequency_template = np.array(np.frombuffer(data.FreqTemplate, dtype='double').reshape(378))

    def load_fit_range_template(self, path):
        with open(path, "r") as open_file:
            raw_data = json.load(open_file)
        self.fit_range_template = {entry["Probe ID"]: (entry["Fid Begin"], entry["Fid End"]) for entry in raw_data}

    def apply_smoothing(self, flux, MaxWidth=1000, start=0, end=4096):
        nWidth = int(np.min([self.smoothWidth, MaxWidth]))
        smoothed = flux[:]
        for iter in range(self.smooth_iterations):
            smoothed = np.array([np.mean(smoothed[max([j-(nWidth-1), start]):min([j+nWidth, end])]) for j in range(start, end)])
        return smoothed

    def phase_from_fft(self, time, flux, WindowFilterLow=0., WindowFilterHigh=200000.):
        # identical to hilbert except the filter line
        freq = fftfreq(len(flux), d=np.diff(time)[0]/s)
        fid_fft_filtered = fft(flux)
        fid_fft_filtered[np.logical_not(np.logical_and(WindowFilterLow<=np.abs(freq), np.abs(freq)<=WindowFilterHigh))] = 0+0j
        filtered_wf = np.real(ifft(fid_fft_filtered))
        wf_im = np.real(ifft(fid_fft_filtered*(-1j)*np.sign(freq)))

        phi = np.arctan2(wf_im, filtered_wf)
        env = np.sqrt(filtered_wf**2 + wf_im**2)
        jump = 1*(phi[:-1] - phi[1:] > 4.71)
        jump -= 1*(phi[1:] - phi[:-1] > 4.71)
        phi += np.concatenate([[0], 2*np.pi*np.cumsum(jump)])
        return filtered_wf, phi, env

    def linear_fit(self, x, y, start, stop, NPar):
        N_Eq = stop - start + 1
        MatrixData = np.array([[x[start+i]**j for j in range(NPar)] for i in range(N_Eq)], dtype=np.float64)
        RHSData = np.array(y[start:stop+1], dtype=np.float64)
        M = np.matmul(MatrixData.T,MatrixData)
        b = np.matmul(MatrixData.T,RHSData)
        #M_inv = np.linalg.inv(M)
        #M_inv = np.linalg.pinv(M)
        M_inv = MatrixInvertRoot(M, tol=1e-32)
        solution = np.matmul(M_inv,b)
        return solution[1], solution[0], None, None, None

    def get_fit_range(self, env, filtered_wf, dt):
        Length = len(env)
        nIgnore = np.floor(self.edge_ignore/dt);

        # Find the maximum of the envelope
        k = np.argmax(env[nIgnore:-nIgnore]) + nIgnore + nIgnore # bug

        #Start from the next falling zero-crossing
        while True:
            if ((filtered_wf[k]>=0 and filtered_wf[k+1]<0) or k>=Length-nIgnore-1): break
            k+=1
        idx_start = k-2 if k>=0 else k

        # Find the point where the amplitude dropped to fraction
        rel = env[k]
        while True:
            if env[k]<rel*self.start_amplitude or k>=Length-nIgnore: break
            k+=1

        #End at the previous falling zero-crossing
        while True:
            if (k<=nIgnore+1): break
            if (filtered_wf[k]<=0 and filtered_wf[k-1]>0): break
            k-=1
        idx_stop = k+2 if k<=Length-3 else k

        return idx_start, idx_stop

    def fit(self, times, fluxes, probe_id):
        time = times + self.t0
        dt = np.diff(time)[0]/s
        const_baseline = np.mean(fluxes[self.baseline_start:self.baseline_end])
        flux = fluxes - const_baseline
        filtered_wf, phase_raw, env = self.phase_from_fft(time, flux) # same as hilbert but with additional filtering
        if self.use_phase_template:
            phase_raw = phase_raw - self.phase_template[probe_id]
        if self.use_fit_range_template:
            idx_start, idx_stop = self.fit_range_template[probe_id][0], self.fit_range_template[probe_id][1]
        else:
            idx_start, idx_stop = self.get_fit_range(env, filtered_wf, dt)
        #f_estimate, offset_estimate, _, _, _ = linregress(time[idx_start:idx_stop], phase_raw[idx_start:idx_stop])
        f_estimate, offset_estimate, _, _, _ = self.linear_fit(time/s, phase_raw, idx_start, idx_stop, 2)
        f_estimate = f_estimate/(2*np.pi)
        if self.use_phase_template:
            f_estimate += self.frequency_template[probe_id]
        self.smoothWidth = np.floor(1/f_estimate/dt) if 20000 <= f_estimate <= 100000 else np.floor(1/51000/dt)
        phase = self.apply_smoothing(phase_raw)
        idx_stop_short = idx_start + int(np.round((idx_stop-idx_start)*self.LengthReduction))
        #freq, offset, _, _, _ = linregress(time[idx_start:idx_stop], phase[idx_start:idx_stop])
        freq, offset, _, _, _ = self.linear_fit(time/s, phase, idx_start, idx_stop_short, 2)
        freq = freq/(2*np.pi)
        if self.use_phase_template:
            freq += self.frequency_template[probe_id]
        return freq

class PhaseFitEcho(PhaseFitFID):
    def __init__(self, frac=np.exp(-1), probe=None, smoothing=True, tol=1e-5, n_smooth=3):
        self.t0 = 2*probe.readout_length-probe.time_pretrigger
        self.pretrigger = probe.time_pretrigger
        self.readout_length = probe.readout_length
        self.frac = frac
        self.smoothing = True
        self.tol = tol
        self.n_smooth = n_smooth

    def get_fit_range(self):
        # closest index to t0
        idx = np.argmin(np.abs(self.time - self.t0))
        # threshold relative to t0
        thres = self.frac * self.env[idx]

        t_start = np.max(self.time[np.logical_and(self.time < self.t0, self.env < thres)])
        t_stop = np.min(self.time[np.logical_and(self.time > self.t0, self.env < thres)])
        return np.array([t_start, t_stop])

    def plot(self):
        plt.plot(self.time/ms, self.phase_raw - self.phi0 - self.frequency*(self.time-self.t0), color="b", label="raw FID")
        if self.smoothing:
            plt.plot(self.time/ms, self.phase - self.phi0 - self.frequency*(self.time-self.t0), color="red", label="smoothed FID")
        #plt.plot(time[mask]/ms, phi_fit[mask] - phi0 - frequency*(time[mask]-t0), color="k", ls="--", label="fit")
        phase_fit = self.fit_func((self.time-self.t0)/self.width, self.res.x)
        plt.plot(self.time/ms, phase_fit - self.phi0 - self.frequency*(self.time-self.t0), color="k", ls="--", label="fit")
        plt.grid()
        plt.axvspan(*(self.t_range/ms), color="gray", alpha=0.2)
        plt.ylim(-0.1, 0.2)
        plt.xlabel("time / ms")
        plt.xlim( (np.mean(self.t_range)-0.55*(self.t_range[1]-self.t_range[0]))/ms, (np.mean(self.t_range)+0.55*(self.t_range[1]-self.t_range[0]))/ms)
        plt.ylabel(r"$ \Phi(t) - \hat{\Phi_0} - \hat{\frac{\mathrm{d}\Phi}{\mathrm{d}t}}\cdot t$")
        plt.legend()
        plt.axvline(self.t0/ms, ls="--", color="k")
        plt.text(self.t0/ms, 0.20, "Echo", rotation=90, va="top", ha="left", fontsize=12, fontweight='bold')
