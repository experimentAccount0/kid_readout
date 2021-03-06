from __future__ import division

from collections import namedtuple

import numpy as np
from matplotlib import mlab
from matplotlib.mlab import cbook

from kid_readout.analysis.timeseries import binning


AutoAutoCross = namedtuple('AutoAutoCross', field_names=['f', 'S_aa', 'S_bb', 'S_ab'])


def auto_auto_cross(a, b, sample_rate, NFFT=None, detrend=mlab.detrend_none, window=mlab.window_none, noverlap=None,
                    binned=True, bins_per_decade=30, **kwds):
    """
    Return estimates of the auto-spectral density of both real time series a and b, of their cross-spectral density, and
    the frequencies corresponding to these estimates.

    Parameters
    ----------
    a : ndarray(real)
        A real time series.
    b : ndarray(real)
        A real time series.
    sample_rate : float
        The sample rate of both time series.
    NFFT : int
        The number of samples to use for each FFT chunk; should be a power of two for speed; if None, a reasonable
        default is used.
    window  : callable
        A function that takes a complex time series as argument and returns a windowed time series.
    noverlap : int
        The number of samples to overlap in each chunk; if None, a value equal to half the NFFT value is used.
    detrend : callable
        A function that takes a complex time series as argument and returns a detrended time series.
    binned : bool
        If True, the result is binned using bin sizes that increase with frequency, and the bins at zero frequency and
        the Nyquist frequency are dropped.
    bins_per_decade : int
        The number of bins per decade; used only if binned is True.
    kwds : dict
        Additional keywords to pass to mlab.psd and mlab.csd.

    Returns
    -------
    f : ndarray(float)
        The frequencies corresponding to the data.
    S_aa : ndarray(float)
        The spectral density of a.
    S_bb : ndarray(float)
        The spectral density of b.
    S_ab : ndarray(complex)
        The cross-spectral density of a and b.
    """
    if NFFT is None:
        NFFT = int(2 ** (np.floor(np.log2(a.size)) - 3))
    if noverlap is None:
        noverlap = NFFT // 2
    S_aa, f = mlab.psd(a, Fs=sample_rate, NFFT=NFFT, detrend=detrend, window=window, noverlap=noverlap, **kwds)
    S_bb, f = mlab.psd(b, Fs=sample_rate, NFFT=NFFT, detrend=detrend, window=window, noverlap=noverlap, **kwds)
    S_ab, f = mlab.csd(a, b, Fs=sample_rate, NFFT=NFFT, window=window, detrend=detrend, noverlap=noverlap, **kwds)
    if binned:
        f = f[1:-1]
        S_aa = S_aa[1:-1]
        S_bb = S_bb[1:-1]
        S_ab = S_ab[1:-1]
        edges, counts, f, (S_aa, S_bb, S_ab) = binning.log_bin(f, bins_per_decade, S_aa, S_bb, S_ab)
    return AutoAutoCross(f, S_aa, S_bb, S_ab)


def pca_noise_with_errors(d, NFFT, Fs, window=mlab.window_hanning, detrend=mlab.detrend_mean,
                          use_log_bins=True):
    # Assume the rotation is small so that the variance can be approximated using the values in the pre-PCA spectra.
    # Take the variance to be the square of the power in each bin, divided by the number of averaged spectra.
    n_averaged = d.size / NFFT
    pii, pf = mlab.psd(d.real, NFFT=NFFT, Fs=Fs, window=window, detrend=detrend)
    pqq, pf = mlab.psd(d.imag, NFFT=NFFT, Fs=Fs, window=window, detrend=detrend)
    piq, pf = mlab.csd(d.real, d.imag, NFFT=NFFT, Fs=Fs, window=window, detrend=detrend)
    if use_log_bins:
        bf, bc_ii, (bp_ii, bvar_ii) = binning.log_bin_with_variance(pf, pii, pii ** 2 / n_averaged)
        bf, bc_qq, (bp_qq, bvar_qq) = binning.log_bin_with_variance(pf, pqq, pqq ** 2 / n_averaged)
        bf, bc_iq, (bp_iq, bvar_iq) = binning.log_bin_with_variance(pf, piq, np.abs(piq) ** 2 / n_averaged)  # probably not right
        S, evals, evects, angles = calculate_pca_noise(bp_ii, bp_qq, bp_iq)
        return bf, S, evals, evects, angles, (bp_ii, bp_qq, bp_iq), bc_ii, np.vstack((bvar_qq, bvar_ii))
    else:
        S, evals, evects, angles = calculate_pca_noise(pii, pqq, piq)
        return (pf, S, evals, evects, angles, (pii, piq, piq), np.ones_like(pf),
                np.vstack((pqq**2 / n_averaged, pii**2 / n_averaged)))


def pca_noise(d, NFFT=None, Fs=256e6/2.**11, window=mlab.window_hanning, detrend=mlab.detrend_mean,
              use_log_bins=True, use_full_spectral_helper=True):
    if NFFT is None:
        NFFT = int(2 ** (np.floor(np.log2(d.shape[0])) - 3))
        #print "using NFFT: 2**", np.log2(NFFT)
    if use_full_spectral_helper:
        pii, pqq, piq, fr_orig, t = full_spectral_helper(d.real, d.imag, NFFT=NFFT, Fs=Fs, window=window,
                                                         detrend=detrend)
        pii = pii.mean(1)
        pqq = pqq.mean(1)
        piq = piq.mean(1)
    else:
        pii, fr_orig = mlab.psd(d.real, NFFT=NFFT, Fs=Fs, window=window, detrend=detrend)
        pqq, fr = mlab.psd(d.imag, NFFT=NFFT, Fs=Fs, window=window, detrend=detrend)
        piq, fr = mlab.csd(d.real, d.imag, NFFT=NFFT, Fs=Fs, window=window, detrend=detrend)
    if use_log_bins:
        fr, (pii, pqq, piq) = binning.log_bin_old(fr_orig, [pii, pqq, piq])
    else:
        fr = fr_orig
    S, evals, evects, angles = calculate_pca_noise(pii, pqq, piq)
    return fr, S, evals, evects, angles, piq


def calculate_pca_noise(pii, pqq, piq):
    nf = pii.shape[0]
    evals = np.zeros((2, nf))  # since the matrix is hermetian, eigvals are real
    evects = np.zeros((2, 2, nf), dtype='complex')
    for k in range(nf):
        m = np.array([[pii[k], np.real(piq[k])],
                      [np.conj(np.real(piq[k])), pqq[k]]])
        w, v = np.linalg.eigh(m)
        evals[:, k] = w
        evects[:, :, k] = v
    angles = np.zeros((2, nf))
    angles[0, :] = np.mod(np.arctan2(evects[0, 0, :].real, evects[1, 0, :].real), np.pi)
    angles[1, :] = np.mod(np.arctan2(evects[0, 1, :].real, evects[1, 1, :].real), np.pi)
    S = np.zeros((2, nf))
    v = evects[:, :, 0]
    invv = np.linalg.inv(v)
    for k in range(nf):
        m = np.array([[pii[k], piq[k]],
                      [np.conj(piq[k]), pqq[k]]])
        ss = np.dot(np.dot(invv, m), v)
        S[0, k] = ss[0, 0]
        S[1, k] = ss[1, 1]
    return S, evals, evects, angles


# the following based on matplotlib mlab module, and avoids duplicated computation when auto and cross densities are
# needed.
def full_spectral_helper(x, y, NFFT=256, Fs=2, detrend=mlab.detrend_none,
                         window=mlab.window_hanning, noverlap=0, pad_to=None, sides='default',
                         scale_by_freq=None):
    """
    Compute all auto and cross spectral densities of x and y at once to save time
    :param x:
    :param y:
    :param NFFT:
    :param Fs:
    :param detrend:
    :param window:
    :param noverlap:
    :param pad_to:
    :param sides:
    :param scale_by_freq:
    :return:
    """
    # The checks for if y is x are so that we can use the same function to
    #implement the core of psd(), csd(), and spectrogram() without doing
    #extra calculations.  We return the unaveraged Pxy, freqs, and t.
    same_data = y is x

    #Make sure we're dealing with a numpy array. If y and x were the same
    #object to start with, keep them that way
    x = np.asarray(x)
    if not same_data:
        y = np.asarray(y)
    else:
        y = x

    # zero pad x and y up to NFFT if they are shorter than NFFT
    if len(x) < NFFT:
        n = len(x)
        x = np.resize(x, (NFFT,))
        x[n:] = 0

    if not same_data and len(y) < NFFT:
        n = len(y)
        y = np.resize(y, (NFFT,))
        y[n:] = 0

    if pad_to is None:
        pad_to = NFFT

    if scale_by_freq is None:
        scale_by_freq = True

    # For real x, ignore the negative frequencies unless told otherwise
    if (sides == 'default' and np.iscomplexobj(x)) or sides == 'twosided':
        numFreqs = pad_to
        scaling_factor = 1.
    elif sides in ('default', 'onesided'):
        numFreqs = pad_to // 2 + 1
        scaling_factor = 2.
    else:
        raise ValueError("sides must be one of: 'default', 'onesided', or "
                         "'twosided'")

    if cbook.iterable(window):
        assert (len(window) == NFFT)
        windowVals = window
    else:
        windowVals = window(np.ones((NFFT,), x.dtype))

    step = NFFT - noverlap
    ind = np.arange(0, len(x) - NFFT + 1, step)
    n = len(ind)
    Pxx = np.zeros((numFreqs, n), np.float_)
    Pyy = np.zeros((numFreqs, n), np.float_)
    Pxy = np.zeros((numFreqs, n), np.complex_)

    # do the ffts of the slices
    for i in range(n):
        thisX = x[ind[i]:ind[i] + NFFT]
        thisX = windowVals * detrend(thisX)
        fx = np.fft.fft(thisX, n=pad_to)

        if same_data:
            fy = fx
        else:
            thisY = y[ind[i]:ind[i] + NFFT]
            thisY = windowVals * detrend(thisY)
            fy = np.fft.fft(thisY, n=pad_to)
        Pxy[:, i] = np.conjugate(fx[:numFreqs]) * fy[:numFreqs]
        Pxx[:, i] = np.conjugate(fx[:numFreqs]) * fx[:numFreqs]
        Pyy[:, i] = np.conjugate(fy[:numFreqs]) * fy[:numFreqs]

    # Scale the spectrum by the norm of the window to compensate for
    # windowing loss; see Bendat & Piersol Sec 11.5.2.
    Pxy /= (np.abs(windowVals) ** 2).sum()
    Pxx /= (np.abs(windowVals) ** 2).sum()
    Pyy /= (np.abs(windowVals) ** 2).sum()

    # Also include scaling factors for one-sided densities and dividing by the
    # sampling frequency, if desired. Scale everything, except the DC component
    # and the NFFT/2 component:
    Pxy[1:-1] *= scaling_factor
    Pxx[1:-1] *= scaling_factor
    Pyy[1:-1] *= scaling_factor

    # MATLAB divides by the sampling frequency so that density function
    # has units of dB/Hz and can be integrated by the plotted frequency
    # values. Perform the same scaling here.
    if scale_by_freq:
        Pxy /= Fs
        Pyy /= Fs
        Pxx /= Fs

    t = 1. / Fs * (ind + NFFT / 2.)
    freqs = float(Fs) / pad_to * np.arange(numFreqs)

    if (np.iscomplexobj(x) and sides == 'default') or sides == 'twosided':
        # center the frequency range at zero
        freqs = np.concatenate((freqs[numFreqs // 2:] - Fs, freqs[:numFreqs // 2]))
        Pxy = np.concatenate((Pxy[numFreqs // 2:, :], Pxy[:numFreqs // 2, :]), 0)
        Pxx = np.concatenate((Pxx[numFreqs // 2:, :], Pxx[:numFreqs // 2, :]), 0)
        Pyy = np.concatenate((Pyy[numFreqs // 2:, :], Pyy[:numFreqs // 2, :]), 0)

    return Pxx, Pyy, Pxy, freqs, t
