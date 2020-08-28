import numpy as np
from scipy.signal import hilbert
from ..units import uV, ms

class HilbertTransform(object):
    def __init__(self, times, flux):
        if len(times) != len(flux):
            raise AttributeError("times and flux must have the same dimension")
        self.N = len(flux)
        self.time = times / ms
        self.flux = flux / uV
        self.h = hilbert(self.flux)

    def real(self):
        return np.real(self.h)

    def imag(self):
        return np.imag(self.h)

    def PhaseFunction(self):
        phi = np.arctan2(self.imag(), self.real())
        phi = np.arctan(self.imag()/self.real())
        oscillations = np.cumsum(np.pi*(np.sign(self.flux[1:]) != np.sign(self.flux[:-1])))
        phi += np.concatenate([[0], oscillations])
        return self.time, phi

    def EnvelopeFunction(self):
        return self.time, np.sqrt(self.imag()**2 + self.real()**2)

    def plot_phase_function(self, fig=None):
        if fig is None:
            fig, ax = plt.subplots()

        t, phi = self.PhaseFunction()
        ax.scatter(t, np.degrees(phi))
