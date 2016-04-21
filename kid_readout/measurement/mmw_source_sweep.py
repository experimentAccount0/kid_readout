from __future__ import division
from kid_readout.measurement import core, basic
import numpy as np
import pandas as pd
from memoized_property import memoized_property
# The ZBD object loads a few data files from disk. If this import fails then the functions that use it below will still
# work, but only with default arguments.
try:
    from equipment.vdi.zbd import ZBD
    zbd = ZBD()
except ImportError:
    zbd = None


class MMWSweepList(basic.SweepStreamList):

    def __init__(self, sweep, stream_list, state, description=''):
        super(MMWSweepList, self).__init__(sweep=sweep, stream_list=stream_list, state=state, description=description)

    def single_sweep_stream_list(self, index):
        return MMWResponse(self.sweep.sweep(index),
                           core.MeasurementList(sa.stream(index) for sa in self.stream_list),
                           state=self.state, description=self.description)


class MMWResponse(basic.SingleSweepStreamList):

    def __init__(self, single_sweep, stream_list, state, description=''):
        super(MMWResponse,self).__init__(single_sweep=single_sweep, stream_list=stream_list, state=state,
                                         description=description)

    @property
    def lockin_rms_voltage(self):
        return np.array(self.state_vector('lockin','rms_voltage'),dtype='float')

    def zbd_power(self, linearize=False):
        return zbd_voltage_to_power(self.zbd_voltage(linearize=linearize), mmw_frequency=self.mmw_frequency)

    def zbd_voltage(self, linearize=False):
        return lockin_rms_to_zbd_voltage(self.lockin_rms_voltage, linearize=linearize)

    @property
    def hittite_frequency(self):
        return np.array(self.state_vector('hittite','frequency'), dtype='float')

    @property
    def mmw_frequency(self):
        return 12.*self.hittite_frequency

    @memoized_property
    def sweep_stream_list(self):
        return self.get_sweep_stream_list()

    def get_sweep_stream_list(self, deglitch=False):
        result = []
        for stream in self.stream_list:
            sss = basic.SingleSweepStream(sweep=self.sweep, stream=stream, state=stream.state,
                                          description=stream.description)
            sss._set_q_and_x(deglitch=deglitch)
            result.append(sss)
        return result

    @memoized_property
    def folded_x(self):
        sweep_stream_list = self.sweep_stream_list
        result = []
        for sss in sweep_stream_list:
            fx = sss.fold(sss.x)
            result.append(fx)
        return np.array(result)

    @memoized_property
    def folded_normalized_s21(self):
        sweep_stream_list = self.sweep_stream_list
        result = []
        for sss in sweep_stream_list:
            fs21 = sss.fold(sss.normalized_s21)
            result.append(fs21)
        return np.array(result)

    @memoized_property
    def fractional_frequency_response(self):
        return self.get_fractional_frequency_response()

    def get_fractional_frequency_response(self):
        folded = self.folded_x
        period = folded.shape[-1]
        return np.abs(folded[...,period//8:3*period//8].mean(-1) - folded[...,5*period//8:7*period//8].mean(-1))


class MMWSweepOnMod(core.Measurement):

    def __init__(self, sweep, on_stream, mod_stream, state=None, description=''):
        self.sweep = self.add_measurement(sweep)
        self.on_stream = self.add_measurement(on_stream)
        self.mod_stream = self.add_measurement(mod_stream)
        super(MMWSweepOnMod, self).__init__(state=state, description=description)

    @property
    def on_sweep_stream_array(self):
        return basic.SweepStreamArray(sweep_array=self.sweep, stream_array=self.on_stream,state=self.state,
                                      description=self.description)
    @property
    def mod_sweep_stream_array(self):
        return basic.SweepStreamArray(sweep_array=self.sweep, stream_array=self.mod_stream,state=self.state,
                                      description=self.description)

    def sweep_stream_pair(self,number):
        sweep = self.sweep.sweep(number)
        on_sweep_stream = self.on_stream.stream(number)
        mod_sweep_stream = self.mod_stream.stream(number)
        return (basic.SingleSweepStream(sweep,on_sweep_stream,number=number,state=self.state,
                                        description=self.description),
                basic.SingleSweepStream(sweep,mod_sweep_stream,number=number,state=self.state,
                                        description=self.description),
                )


    def to_dataframe(self, add_origin=True):
        on_rows = []
        mod_rows = []
        for n in range(self.sweep.num_channels):
            on_ss, mod_ss = self.sweep_stream_pair(n)
            on_rows.append(on_ss.to_dataframe(add_origin=False))
            mod_rows.append(mod_ss.to_dataframe(deglitch=False,add_origin=False))
        df_on = pd.concat(on_rows,ignore_index=True)
        df_mod = pd.concat(mod_rows,ignore_index=True)
        if add_origin:
            if self._io_class is None:
                self.sweep.add_origin(df_on,prefix='sweep_')
                self.on_stream.add_origin(df_on,prefix='stream_')
                self.sweep.add_origin(df_mod,prefix='sweep_')
                self.mod_stream.add_origin(df_mod,prefix='stream_')
            else:
                self.add_origin(df_on)
                self.add_origin(df_mod)
        df_on['lockin_rms_voltage'] = df_mod['lockin_rms_voltage']
        return pd.concat((df_on,df_mod),ignore_index=True)
        
        
def lockin_rms_to_zbd_voltage(lockin_rms_voltage, linearize=False):
    zbd_voltage = (np.pi / np.sqrt(2)) * lockin_rms_voltage
    if linearize:
        zbd_voltage /= zbd.linearity(zbd_voltage)
    return zbd_voltage


def zbd_voltage_to_power(zbd_voltage, mmw_frequency=None):
    if mmw_frequency is None:
        volts_per_watt = 2200  # 2200 V/W is the approximate responsivity
    else:
        volts_per_watt = zbd.responsivity(mmw_frequency)
    return zbd_voltage / volts_per_watt
