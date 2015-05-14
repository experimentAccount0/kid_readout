import socket
import time

import numpy as np
import scipy.signal
from kid_readout.roach.interface import RoachInterface
from kid_readout.roach.tools import compute_window
import kid_readout.roach.udp_catcher


try:
    import numexpr

    have_numexpr = True
except ImportError:
    have_numexpr = False


class RoachHeterodyne(RoachInterface):

    def __init__(self, roach=None, wafer=0, roachip='roach', adc_valon=None, host_ip=None):
        """
        Class to represent the heterodyne readout system (high-frequency (1.5 GHz), IQ mixers)

        roach: an FpgaClient instance for communicating with the ROACH.
                If not specified, will try to instantiate one connected to *roachip*
        wafer: 0
                Not used for heterodyne system
        roachip: (optional). Network address of the ROACH if you don't want to provide an FpgaClient
        adc_valon: a Valon class, a string, or None
                Provide access to the Valon class which controls the Valon synthesizer which provides
                the ADC and DAC sampling clock.
                The default None value will use the valon.find_valon function to locate a synthesizer
                and create a Valon class for you.
                You can alternatively pass a string such as '/dev/ttyUSB0' to specify the port for the
                synthesizer, which will then be used for creating a Valon class.
                Finally, for test suites, you can directly pass a Valon class or a class with the same
                interface.
        """
        if roach:
            self.r = roach
        else:
            from corr.katcp_wrapper import FpgaClient
            self.r = FpgaClient(roachip)
            t1 = time.time()
            timeout = 10
            while not self.r.is_connected():
                if (time.time() - t1) > timeout:
                    raise Exception("Connection timeout to roach")
                time.sleep(0.1)

        if adc_valon is None:
            from kid_readout.utils import valon
            ports = valon.find_valons()
            if len(ports) == 0:
                self.adc_valon_port = None
                self.adc_valon = None
                print "Warning: No valon found!"
            else:
                for port in ports:
                    try:
                        self.adc_valon_port = port
                        self.adc_valon = valon.Synthesizer(port)
                        f = self.adc_valon.get_frequency_a()
                        break
                    except:
                        pass
        elif type(adc_valon) is str:
            from kid_readout.utils import valon
            self.adc_valon_port = adc_valon
            self.adc_valon = valon.Synthesizer(self.adc_valon_port)
        else:
            self.adc_valon = adc_valon

        self.adc_atten = 31.5
        self.dac_atten = -1
        self.fft_bins = None
        self.tone_nsamp = None
        self.tone_bins = None
        self.phases = None
        self.modulation_output = 0
        self.modulation_rate = 0
        self.lo_frequency = 0.0
        self.get_current_bank()

        self.bof_pid = None
        self.roachip = roachip
        if host_ip is None:
            hostname = socket.gethostname()
            if hostname == 'detectors':
                host_ip = '192.168.1.1'
            else:
                host_ip = '192.168.1.1'
        self.host_ip = host_ip

        try:
            self.fs = self.adc_valon.get_frequency_a()
        except:
            print "warning couldn't get valon frequency, assuming 512 MHz"
            self.fs = 512.0
        self.wafer = wafer
        self.dac_ns = 2 ** 16  # number of samples in the dac buffer
        self.raw_adc_ns = 2 ** 12  # number of samples in the raw ADC buffer
        self.nfft = 2 ** 14
        self.boffile = 'iq2xpfb14mcr4_2013_Aug_02_1446.bof'
        self.boffile = 'iq2xpfb14mcr6_2015_May_11_2241.bof'
        self.bufname = 'ppout%d' % wafer
        self._window_mag = compute_window(npfb=self.nfft, taps=2, wfunc=scipy.signal.flattop)

    def load_waveforms(self, i_wave, q_wave, fast=True):
        """
        Load waveforms for the two DACs

        i_wave,q_wave : arrays of 16-bit (dtype='i2') integers with waveforms for the two DACs

        fast : boolean
            decide what method for loading the dram
        """
        data = np.zeros((2 * i_wave.shape[0],), dtype='>i2')
        data[0::4] = i_wave[::2]
        data[1::4] = i_wave[1::2]
        data[2::4] = q_wave[::2]
        data[3::4] = q_wave[1::2]
        self.r.write_int('dram_mask', data.shape[0] / 4 - 1)
        self._load_dram(data, fast=fast)

    def set_tone_freqs(self, freqs, nsamp, amps=None):
        """
        Set the stimulus tones to generate

        freqs : array of frequencies in MHz
            For Heterodyne system, these can be positive or negative to produce tones above and
            below the local oscillator frequency.
        nsamp : int, must be power of 2
            number of samples in the playback buffer. Frequency resolution will be fs/nsamp
        amps : optional array of floats, same length as freqs array
            specify the relative amplitude of each tone. Can set to zero to read out a portion
            of the spectrum with no stimulus tone.

        returns:
        actual_freqs : array of the actual frequencies after quantization based on nsamp
        """
        bins = np.round((freqs / self.fs) * nsamp).astype('int')
        actual_freqs = self.fs * bins / float(nsamp)
        bins[bins < 0] = nsamp + bins[bins < 0]
        self.set_tone_bins(bins, nsamp, amps=amps)
        self.fft_bins = self.calc_fft_bins(bins, nsamp)
        if self.fft_bins.shape[0] > 4:
            readout_selection = range(4)
        else:
            readout_selection = range(self.fft_bins.shape[1])

        self.select_fft_bins(readout_selection)
        return actual_freqs

    def set_tone_bins(self, bins, nsamp, amps=None, load=True, normfact=None,phases=None):
        """
        Set the stimulus tones by specific integer bins

        bins : array of bins at which tones should be placed
            For Heterodyne system, negative frequencies should be placed in cannonical FFT order
            If 2d, interpret as (nwaves,ntones)
        nsamp : int, must be power of 2
            number of samples in the playback buffer. Frequency resolution will be fs/nsamp
        amps : optional array of floats, same length as bins array
            specify the relative amplitude of each tone. Can set to zero to read out a portion
            of the spectrum with no stimulus tone.
        load : bool (debug only). If false, don't actually load the waveform, just calculate it.
        """

        if bins.ndim == 1:
            bins.shape = (1, bins.shape[0])
        nwaves = bins.shape[0]
        spec = np.zeros((nwaves, nsamp), dtype='complex')
        self.tone_bins = bins.copy()
        self.tone_nsamp = nsamp
        if phases is None:
            phases = np.random.random(bins.shape[1]) * 2 * np.pi
        self.phases = phases.copy()
        if amps is None:
            amps = 1.0
        self.amps = amps
        for k in range(nwaves):
            spec[k, bins[k, :]] = amps * np.exp(1j * phases)
        wave = np.fft.ifft(spec, axis=1)
        self.wavenorm = np.abs(wave).max()
        if normfact is not None:
            wn = (2.0 / normfact) * len(bins) / float(nsamp)
            print "ratio of current wavenorm to optimal:", self.wavenorm / wn
            self.wavenorm = wn
        q_rwave = np.round((wave.real / self.wavenorm) * (2 ** 15 - 1024)).astype('>i2')
        q_iwave = np.round((wave.imag / self.wavenorm) * (2 ** 15 - 1024)).astype('>i2')
        q_rwave.shape = (q_rwave.shape[0] * q_rwave.shape[1],)
        q_iwave.shape = (q_iwave.shape[0] * q_iwave.shape[1],)
        self.q_rwave = q_rwave
        self.q_iwave = q_iwave
        if load:
            self.load_waveforms(q_rwave,q_iwave)
        self.save_state()

    def calc_fft_bins(self, tone_bins, nsamp):
        """
        Calculate the FFT bins in which the tones will fall

        tone_bins: array of integers
            the tone bins (0 to nsamp - 1) which contain tones

        nsamp : length of the playback bufffer

        returns: fft_bins, array of integers.
        """
        tone_bins_per_fft_bin = nsamp / (self.nfft)
        fft_bins = np.round(tone_bins / float(tone_bins_per_fft_bin)).astype('int')
        return fft_bins

    def fft_bin_to_index(self, bins):
        """
        Convert FFT bins to FPGA indexes
        """
        idx = bins.copy()
        return idx

    def select_fft_bins(self, readout_selection):
        """
        Select which subset of the available FFT bins to read out

        Initially we can only read out from a subset of the FFT bins, so this function selects which bins to read out right now
        This also takes care of writing the selection to the FPGA with the appropriate tweaks

        The readout selection is stored to self.readout_selection
        The FPGA readout indexes is stored in self.fpga_fft_readout_indexes
        The bins that we are reading out is stored in self.readout_fft_bins

        readout_selection : array of ints
            indexes into the self.fft_bins array to specify the bins to read out
        """
        offset = 4
        idxs = self.fft_bin_to_index(self.fft_bins[self.bank,readout_selection])
        order = idxs.argsort()
        idxs = idxs[order]
        self.readout_selection = np.array(readout_selection)[order]
        self.fpga_fft_readout_indexes = idxs
        self.readout_fft_bins = self.fft_bins[self.bank, self.readout_selection]

        binsel = np.zeros((self.fpga_fft_readout_indexes.shape[0] + 1,), dtype='>i4')
        # evenodd = np.mod(self.fpga_fft_readout_indexes,2)
        #binsel[:-1] = np.mod(self.fpga_fft_readout_indexes/2-offset,self.nfft/2)
        #binsel[:-1] += evenodd*2**16
        binsel[:-1] = np.mod(self.fpga_fft_readout_indexes - offset, self.nfft)
        binsel[-1] = -1
        self.r.write('chans', binsel.tostring())

    def demodulate_data(self, data):
        """
        Demodulate the data from the FFT bin

        This function assumes that self.select_fft_bins was called to set up the necessary class attributes

        data : array of complex data

        returns : demodulated data in an array of the same shape and dtype as *data*
        """
        bank = self.bank
        demod = np.zeros_like(data)
        t = np.arange(data.shape[0])
        for n, ich in enumerate(self.readout_selection):
            phi0 = self.phases[ich]
            k = self.tone_bins[bank,ich]
            m = self.fft_bins[bank,ich]
            if m >= self.nfft / 2:
                sign = -1.0
                doconj = True
            else:
                sign = -1.0
                doconj = False
            nfft = self.nfft
            ns = self.tone_nsamp
            foffs = (k * nfft - m * ns) / float(ns)
            demod[:, n] = np.exp(sign * 1j * (2 * np.pi * foffs * t + phi0)) * data[:, n]
            if doconj:
                demod[:, n] = np.conjugate(demod[:, n])
        return demod

    def get_data(self, nread=2, demod=True):
        return self.get_data_udp(nread=nread, demod=demod)

    def get_data_udp(self, nread=2, demod=True):
        chan_offset = 1
        nch = self.fpga_fft_readout_indexes.shape[0]
        data, seqnos = kid_readout.roach.udp_catcher.get_udp_data(self, npkts=nread * 16 * nch, streamid=1,
                                                chans=self.fpga_fft_readout_indexes//2 + chan_offset,
                                                nfft=self.nfft//2, addr=(self.host_ip, 12345))  # , stream_reg, addr)
        if demod:
            data = self.demodulate_data(data)
        return data, seqnos

    def get_data_katcp(self, nread=10, demod=True):
        """
        Get a chunk of data

        nread: number of 4096 sample frames to read

        demod: should the data be demodulated before returning? Default, yes

        returns  dout,addrs

        dout: complex data stream. Real and imaginary parts are each 16 bit signed
                integers (but cast to numpy complex)

        addrs: counter values when each frame was read. Can be used to check that
                frames are contiguous
        """
        print "getting data"
        bufname = 'ppout%d' % self.wafer
        chan_offset = 1
        draw, addr, ch = self._read_data(nread, bufname)
        if not np.all(ch == ch[0]):
            print "all channel registers not the same; this case not yet supported"
            return draw, addr, ch
        if not np.all(np.diff(addr) < 8192):
            print "address skip!"
        nch = self.readout_selection.shape[0]
        dout = draw.reshape((-1, nch))
        shift = np.flatnonzero(self.fpga_fft_readout_indexes / 2 == (ch[0] - chan_offset))[0] - (nch - 1)
        print shift
        dout = np.roll(dout, shift, axis=1)
        if demod:
            dout = self.demodulate_data(dout)
        return dout, addr

    def set_lo(self, lomhz=1200.0, chan_spacing=2.0):
        """
        Set the local oscillator frequency for the IQ mixers

        lomhz: float, frequency in MHz
        """
        #TODO: Fix this after valon is updated
        self.adc_valon.set_rf_level(8,2)
        self.adc_valon.set_frequency_b(lomhz, chan_spacing=chan_spacing)
        self.lo_frequency = lomhz
        self.save_state()

    def set_dac_attenuator(self, attendb):
        if attendb < 0 or attendb > 63:
            raise ValueError("ADC Attenuator must be between 0 and 63 dB. Value given was: %s" % str(attendb))

        if attendb > 31.5:
            attena = 31.5
            attenb = attendb - attena
        else:
            attena = attendb
            attenb = 0
        self.set_attenuator(attena, le_bit=0x01)
        self.set_attenuator(attenb, le_bit=0x80)
        self.dac_atten = int(attendb * 2) / 2.0

    def set_adc_attenuator(self, attendb):
        if attendb < 0 or attendb > 31.5:
            raise ValueError("ADC Attenuator must be between 0 and 31.5 dB. Value given was: %s" % str(attendb))
        self.set_attenuator(attendb, le_bit=0x02)
        self.adc_atten = int(attendb * 2) / 2.0

    def _set_fs(self, fs, chan_spacing=2.0):
        """
        Set sampling frequency in MHz
        Note, this should generally not be called without also reprogramming the ROACH
        Use initialize() instead
        """
        if self.adc_valon is None:
            print "Could not set Valon; none available"
            return
        self.adc_valon.set_frequency_a(fs, chan_spacing=chan_spacing)
        self.fs = fs