from neo.core import objectlist
from neo.core import Block, Event, Epoch, Segment, AnalogSignal, \
    IrregularlySampledSignal, SpikeTrain, RecordingChannelGroup, Unit
from neo.io.baseio import BaseIO

import numpy as np
import quantities as pq
import nix
import os


# -------------------------------------------
# file operations
# -------------------------------------------


def file_transaction(method):
    """
    A decorator that opens the file before and closes after a given I/O method
    execution.

    :param method:  a method to execute between opening and closing a file.
    :return:        wrapped function
    """
    def wrapped(*args, **kwargs):
        instance = args[0]
        instance.f.open()

        result = method(*args, **kwargs)
        
        instance.f.close()
        return result

    return wrapped


class FileHandler(object):
    """
    Wrapper for NIX.File to provide some extended functions
    """

    def __init__(self, filename, readonly=False):
        self.filename = filename
        self.readonly = readonly
        self.handle = None  # future NIX file handle

    def open(self):
        if os.path.exists(self.filename):
            if self.readonly:
                filemode = nix.FileMode.ReadOnly
            else:
                filemode = nix.FileMode.ReadWrite
        else:
            filemode = nix.FileMode.Overwrite

        self.handle = nix.File.open(self.filename, filemode)

    def close(self):
        self.handle.close()


class ProxyList(object):
    """ An enhanced list that can load its members on demand. """

    def __init__(self, fh, fetch_func):
        """
        :param fh:          FileHandler instance (see above) with file reference
        :param fetch_func:  function to apply to fetch objects
        """
        self._fh = fh
        self._fetch_func = fetch_func
        self._cache = None

    @property
    def _data(self):
        if self._cache is None:
            should_close = False

            if self._fh.handle is None or not self._fh.handle.is_open():
                self._fh.open()
                should_close = True

            self._cache = self._fetch_func(self._fh.handle)

            if should_close:
                self._fh.close()

        return self._cache

    def __getitem__(self, index):
        return self._data.__getitem__(index)

    def __delitem__(self, index):
        self._data.__delitem__(index)

    def __len__(self):
        return self._data.__len__()

    def __setitem__(self, index, value):
        self._data.__setitem__(index, value)

    def insert(self, index, value):
        self._data.insert(index, value)

    def append(self, value):
        self._data.append(value)

    def reverse(self):
        self._data.reverse()

    def extend(self, values):
        self._data.extend(values)

    def remove(self, value):
        self._data.remove(value)

    def __str__(self):
        return '<' + self.__class__.__name__ + '>' + self._data.__str__()

    def __repr__(self):
        return '<' + self.__class__.__name__ + '>' + self._data.__repr__()


# -------------------------------------------
# Reader / Writer
# -------------------------------------------

simple_attrs = {
    'default': ('description', 'file_origin'),
    'block': ('file_datetime', 'rec_datetime', 'index'),
    'segment': ('file_datetime', 'rec_datetime', 'index'),
    'analogsignal': ('name',),
    'irregularlysampledsignal': ('name',),
    'spiketrain': ('name',),
    'event': ('name',),
    'epoch': ('name',),
    'recordingchannelgroup': ('name', 'channel_indexes', 'channel_names'),
    'unit': ()
}


class Reader:
    """
    An interface to read Neo objects from NIX file
    File should be created by NixIO.Writer

    Fully static
    Implements lazy loading for children objects
    """

    class Help:

        @staticmethod
        def get_obj_neo_name(nix_obj):
            if nix_obj.type in ['analogsignal', 'spiketrain', 'event', 'epoch']:
                try:
                    return nix_obj.metadata['name']
                except KeyError:
                    return None
            return nix_obj.name

        @staticmethod
        def read_attributes(nix_section, obj_type):
            result = {}

            for attr_name in simple_attrs['default'] + simple_attrs[obj_type]:
                if attr_name in nix_section:
                    result[attr_name] = nix_section[attr_name]

            return result

        @staticmethod
        def read_annotations(nix_section, obj_type):
            result = {}

            exclude_attrs = simple_attrs['default'] + simple_attrs[obj_type]
            for prop in nix_section.props:
                key = prop.name
                value = nix_section[key]

                if key not in exclude_attrs:
                    result[key] = value

            return result

        """
        @staticmethod
        def read_many_template(fh, block_id, get_collection, obj_type):

            collection = get_collection(fh.handle.blocks[block_id])
            objs = filter(lambda x: x.type == obj_type, collection)

            read_func = getattr(Reader, 'read_' + obj_type)

            return [read_func(fh, block_id, obj.name) for obj in objs]
        """

        @staticmethod
        def read_quantity(nix_section, qname):
            value = nix_section[qname]
            unit = nix_section[qname + '__unit']
            return pq.quantity.Quantity(float(value), unit)

    @staticmethod
    def read_block(fh, block_id):
        def read_segments(nix_file):
            tags = filter(lambda x: x.type == 'segment', nix_file.blocks[block_id].tags)
            return [Reader.read_segment(fh, block_id, tag.name) for tag in tags]

        def read_recordingchannelgroups(nix_file):
            sources = filter(lambda x: x.type == 'recordingchannelgroup', nix_file.blocks[block_id].sources)
            return [Reader.read_RCG(fh, block_id, src.name) for src in sources]

        nix_block = fh.handle.blocks[block_id]

        b = Block(name=nix_block.name)

        for key, value in Reader.Help.read_attributes(nix_block.metadata, 'block').items():
            setattr(b, key, value)

        b.annotations = Reader.Help.read_annotations(nix_block.metadata, 'block')

        setattr(b, 'segments', ProxyList(fh, read_segments))
        setattr(b, 'recordingchannelgroups', ProxyList(fh, read_recordingchannelgroups))

        return b

    @staticmethod
    def read_segment(fh, block_id, seg_id):
        def read_multiple(nix_file, obj_type):
            nix_tag = nix_file.blocks[block_id].tags[seg_id]
            objs = filter(lambda x: x.type == obj_type, nix_tag.references)
            read_func = getattr(Reader, 'read_' + obj_type)
            return [read_func(fh, block_id, da.name) for da in objs]

        nix_block = fh.handle.blocks[block_id]
        nix_tag = nix_block.tags[seg_id]

        seg = Segment(name=nix_tag.name)

        for key, value in Reader.Help.read_attributes(nix_tag.metadata, 'segment').items():
            setattr(seg, key, value)

        seg.annotations = Reader.Help.read_annotations(nix_tag.metadata, 'segment')

        setattr(seg, 'analogsignals', ProxyList(fh, lambda f: read_multiple(f, 'analogsignal')))
        setattr(seg, 'irregularlysampledsignals', ProxyList(fh, lambda f: read_multiple(f, 'irregularlysampledsignal')))
        setattr(seg, 'spiketrains', ProxyList(fh, lambda f: read_multiple(f, 'spiketrain')))
        setattr(seg, 'events', ProxyList(fh, lambda f: read_multiple(f, 'event')))
        setattr(seg, 'epochs', ProxyList(fh, lambda f: read_multiple(f, 'epoch')))

        return seg

    @staticmethod
    def read_RCG(fh, block_id, rcg_id):
        def read_multiple(nix_file, obj_type):
            signals = filter(lambda x: x.type == obj_type, nix_file.blocks[block_id].data_arrays)
            signals = [x for x in signals if nsn in [y.name for y in x.sources]]
            read_func = getattr(Reader, 'read_' + obj_type)
            return [read_func(fh, block_id, da.name) for da in signals]

        def read_units(nix_file):
            units = filter(lambda x: x.type == 'unit', nix_file.blocks[block_id].sources[nsn].sources)
            return [Reader.read_unit(fh, block_id, nsn, unit.name) for unit in units]

        nix_block = fh.handle.blocks[block_id]
        nix_source = nix_block.sources[rcg_id]
        nsn = nix_source.name

        params = {
            'name': nix_source.name,
            'channel_indexes': nix_source.metadata['channel_indexes']
        }
        rcg = RecordingChannelGroup(**params)

        for key, value in Reader.Help.read_attributes(nix_source.metadata, 'recordingchannelgroup').items():
            setattr(rcg, key, value)

        rcg.annotations = Reader.Help.read_annotations(nix_source.metadata, 'recordingchannelgroup')

        setattr(rcg, 'analogsignals', ProxyList(fh, lambda f: read_multiple(f, 'analogsignal')))
        setattr(rcg, 'irregularlysampledsignals', ProxyList(fh, lambda f: read_multiple(f, 'irregularlysampledsignal')))
        setattr(rcg, 'units', ProxyList(fh, read_units))

        return rcg

    @staticmethod
    def read_unit(fh, block_id, rcg_source_id, unit_id):
        def read_spiketrains(nix_file):
            strains = filter(lambda x: x.type == 'spiketrain', nix_file.blocks[block_id].data_arrays)
            strains = [x for x in strains if nsn in [y.name for y in x.sources]]
            return [Reader.read_spiketrain(fh, block_id, da.name) for da in strains]

        nix_block = fh.handle.blocks[block_id]
        nix_rcg_source = nix_block.sources[rcg_source_id]
        nix_source = nix_rcg_source.sources[unit_id]
        nsn = nix_source.name

        rcg = Unit(nix_source.name)

        for key, value in Reader.Help.read_attributes(nix_source.metadata, 'unit').items():
            setattr(rcg, key, value)

        rcg.annotations = Reader.Help.read_annotations(nix_source.metadata, 'unit')

        setattr(rcg, 'spiketrains', ProxyList(fh, read_spiketrains))

        return rcg

    @staticmethod
    def read_analogsignal(fh, block_id, array_id):
        nix_block = fh.handle.blocks[block_id]
        nix_da = nix_block.data_arrays[array_id]

        params = {
            'name': Reader.Help.get_obj_neo_name(nix_da),
            'signal': nix_da[:],  # TODO think about lazy data loading
            'units': nix_da.unit,
            'dtype': nix_da.dtype,
        }

        s_dim = nix_da.dimensions[0]
        sampling = s_dim.sampling_interval * getattr(pq, s_dim.unit)
        if 'hz' in s_dim.unit.lower():
            params['sampling_rate'] = sampling
        else:
            params['sampling_period'] = sampling

        signal = AnalogSignal(**params)
        signal.t_start = Reader.Help.read_quantity(nix_da.metadata, 't_start')

        for key, value in Reader.Help.read_attributes(nix_da.metadata, 'analogsignal').items():
            setattr(signal, key, value)

        signal.annotations = Reader.Help.read_annotations(nix_da.metadata, 'analogsignal')

        return signal

    @staticmethod
    def read_irregularlysampledsignal(fh, block_id, array_id):
        nix_block = fh.handle.blocks[block_id]
        nix_da = nix_block.data_arrays[array_id]

        params = {
            'name': Reader.Help.get_obj_neo_name(nix_da),
            'signal': nix_da[:],  # TODO think about lazy data loading
            'units': nix_da.unit,
            'times': nix_da.dimensions[0].ticks,
            'time_units': nix_da.dimensions[0].unit,
            'dtype': nix_da.dtype,
        }

        signal = IrregularlySampledSignal(**params)

        for key, value in Reader.Help.read_attributes(nix_da.metadata, 'irregularlysampledsignal').items():
            setattr(signal, key, value)

        signal.annotations = Reader.Help.read_annotations(nix_da.metadata, 'irregularlysampledsignal')

        return signal

    @staticmethod
    def read_spiketrain(fh, block_id, array_id):
        nix_block = fh.handle.blocks[block_id]
        nix_da = nix_block.data_arrays[array_id]

        params = {
            'times': nix_da[:],  # TODO think about lazy data loading
            'dtype': nix_da.dtype,
            't_start': Reader.Help.read_quantity(nix_da.metadata, 't_start'),
            't_stop': Reader.Help.read_quantity(nix_da.metadata, 't_stop')
        }

        name = Reader.Help.get_obj_neo_name(nix_da)
        if name:
            params['name'] = name

        if 'left_sweep' in nix_da.metadata:
            params['left_sweep'] = Reader.Help.read_quantity(nix_da.metadata, 'left_sweep')

        if len(nix_da.dimensions) > 0:
            s_dim = nix_da.dimensions[0]
            params['sampling_rate'] = s_dim.sampling_interval * getattr(pq, s_dim.unit)

        if nix_da.unit:
            params['units'] = nix_da.unit

        st = SpikeTrain(**params)

        for key, value in Reader.Help.read_attributes(nix_da.metadata, 'spiketrain').items():
            setattr(st, key, value)

        st.annotations = Reader.Help.read_annotations(nix_da.metadata, 'spiketrain')

        return st

    @staticmethod
    def read_event(fh, block_id, array_id):
        nix_block = fh.handle.blocks[block_id]
        nix_da = nix_block.data_arrays[array_id]

        params = {
            'times': nix_da[:],  # TODO think about lazy data loading
            'labels': [x.encode('UTF-8') for x in nix_da.dimensions[0].labels]
        }

        name = Reader.Help.get_obj_neo_name(nix_da)
        if name:
            params['name'] = name

        event = Event(**params)

        for key, value in Reader.Help.read_attributes(nix_da.metadata, 'event').items():
            setattr(event, key, value)

        event.annotations = Reader.Help.read_annotations(nix_da.metadata, 'event')

        return event


    @staticmethod
    def read_epoch(fh, block_id, array_id):
        nix_block = fh.handle.blocks[block_id]
        nix_da = nix_block.data_arrays[array_id]

        params = {
            'times': nix_da[0],  # TODO think about lazy data loading
            'durations': nix_da[1],  # TODO think about lazy data loading
            'labels': [x.encode('UTF-8') for x in nix_da.dimensions[0].labels]
        }

        name = Reader.Help.get_obj_neo_name(nix_da)
        if name:
            params['name'] = name

        epoch = Epoch(**params)

        for key, value in Reader.Help.read_attributes(nix_da.metadata, 'epoch').items():
            setattr(epoch, key, value)

        epoch.annotations = Reader.Help.read_annotations(nix_da.metadata, 'epoch')

        return epoch


class Writer:
    """
    Class to write Neo objects to NIX

    Fully static
    """

    class Help:
        @staticmethod
        def get_classname(neo_obj):
            return neo_obj.__class__.__name__.lower()

        @staticmethod
        def get_obj_nix_name(neo_obj):
            obj_type = Writer.Help.get_classname(neo_obj)

            if obj_type in ['analogsignal', 'irregularlysampledsignal', 'spiketrain']:
                return str(hash(neo_obj.tostring()))
            elif obj_type in ['event', 'epoch']:
                return str(hash(neo_obj.times.tostring()))
            return neo_obj.name

        @staticmethod
        def extract_metadata(neo_obj):  # pure
            metadata = dict(neo_obj.annotations)

            obj_type = Writer.Help.get_classname(neo_obj)
            for attr_name in simple_attrs['default'] + simple_attrs[obj_type]:
                if getattr(neo_obj, attr_name, None) is not None:
                    metadata[attr_name] = getattr(neo_obj, attr_name)

            return metadata

        @staticmethod
        def get_or_create_section(root_section, group_name, name):
            if not isinstance(root_section, nix.Section):
                group_sec = root_section  # file is a root section for Blocks
            else:
                try:
                    group_sec = root_section.sections[group_name + 's']
                except KeyError:
                    group_sec = root_section.create_section(group_name + 's', group_name)

            try:
                target_sec = group_sec.sections[name]
            except KeyError:
                target_sec = group_sec.create_section(name, group_name)

            return target_sec

        @staticmethod
        def write_metadata(nix_section, dict_to_store):
            def make_nix_values(value):
                if not type(value) in (list, tuple):
                    return [nix.Value(value)]
                return [nix.Value(x) for x in value]

            to_store = dict([(k, v) for k, v in dict_to_store.items() if v is not None])

            for attr_name, value in to_store.items():
                values = make_nix_values(value)

                try:
                    p = nix_section.props[attr_name]

                    if not p.values == values:
                        p.values = values
                except KeyError:
                    p = nix_section.create_property(attr_name, values)

        @staticmethod
        def write_many(nix_block, parent, neo_objs):

            def update_references():
                for name in to_remove:
                    del parent.references[name]

                for name in to_append:
                    parent.references.append(nix_objs[name])

            def update_sources():
                for name in to_remove:
                    del nix_objs[name].sources[parent.name]

                for name in to_append:
                    nix_objs[name].sources.append(parent)

            if not len(neo_objs):
                return
            obj_type = Writer.Help.get_classname(neo_objs[0])

            containers = {
                'segment': nix_block.tags,
                'recordingchannelgroup': nix_block.sources,
                'unit': parent.sources,
                'other': nix_block.data_arrays,
            }

            nix_objs = containers[obj_type] if obj_type in containers else containers['other']
            existing = list(filter(lambda x: x.type == obj_type, nix_objs))
            if isinstance(parent, nix.Source) and not obj_type == 'unit':
                existing = [x for x in existing if parent in x.sources]
            elif isinstance(parent, nix.Tag):
                existing = [x for x in existing if x in parent.references]

            conv = Writer.Help.get_obj_nix_name
            to_remove = set([x.name for x in existing]) - set([conv(x) for x in neo_objs])
            to_append = set([conv(x) for x in neo_objs]) - set([x.name for x in existing])

            write_func = getattr(Writer, 'write_' + obj_type)
            args = (nix_block, parent.name) if obj_type == 'unit' else (nix_block,)
            for obj in neo_objs:
                all_args = args + (obj,)
                write_func(*all_args)

            if isinstance(parent, nix.Source) and not obj_type == 'unit':
                update_sources()

            elif isinstance(parent, nix.Tag):  # All data objects
                update_references()

            else:  # Segments, RCGs, Units
                for name in to_remove:
                    del nix_objs[name]

        @staticmethod
        def clean(nix_block):
            """ clean up: del all arrays with no tag/source """
            def has_references(nix_array):
                return len([x for x in nix_block.tags if nix_array in x.references]) > 0

            def has_sources(nix_array):
                return len(nix_array.sources) > 0

            names = [x.name for x in nix_block.data_arrays]
            for name in names:
                da = nix_block.data_arrays[name]
                if not has_references(da) and not has_sources(da):
                    del nix_block.data_arrays[name]

    @staticmethod
    def write_block(nix_file, block, recursive=True):
        try:
            nix_block = nix_file.blocks[block.name]
        except KeyError:
            nix_block = nix_file.create_block(block.name, 'block')

        nix_block.metadata = Writer.Help.get_or_create_section(nix_file, 'block', block.name)
        Writer.Help.write_metadata(nix_block.metadata, Writer.Help.extract_metadata(block))

        if recursive:
            Writer.Help.write_many(nix_block, nix_block, block.segments)
            Writer.Help.write_many(nix_block, nix_block, block.recordingchannelgroups)

        return nix_block

    @staticmethod
    def write_segment(nix_block, segment, recursive=True):
        try:
            nix_tag = nix_block.tags[segment.name]
        except KeyError:
            nix_tag = nix_block.create_tag(segment.name, 'segment', [0.0])

        nix_tag.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'segment', segment.name)
        Writer.Help.write_metadata(nix_tag.metadata, Writer.Help.extract_metadata(segment))

        if recursive:
            Writer.Help.write_many(nix_block, nix_tag, segment.analogsignals)
            Writer.Help.write_many(nix_block, nix_tag, segment.irregularlysampledsignals)
            Writer.Help.write_many(nix_block, nix_tag, segment.spiketrains)
            Writer.Help.write_many(nix_block, nix_tag, segment.events)
            Writer.Help.write_many(nix_block, nix_tag, segment.epochs)

        Writer.Help.clean(nix_block)
        return nix_tag

    @staticmethod
    def write_recordingchannelgroup(nix_block, rcg, recursive=True):
        try:
            nix_source = nix_block.sources[rcg.name]
        except KeyError:
            nix_source = nix_block.create_source(rcg.name, 'recordingchannelgroup')

        nix_source.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'recordingchannelgroup', rcg.name)
        Writer.Help.write_metadata(nix_source.metadata, Writer.Help.extract_metadata(rcg))

        if recursive:
            Writer.Help.write_many(nix_block, nix_source, rcg.units)
            Writer.Help.write_many(nix_block, nix_source, rcg.analogsignals)
            Writer.Help.write_many(nix_block, nix_source, rcg.irregularlysampledsignals)

        Writer.Help.clean(nix_block)
        return nix_source

    @staticmethod
    def write_unit(nix_block, source_id, unit, recursive=True):
        nix_rcg_source = nix_block.sources[source_id]

        try:
            nix_source = nix_rcg_source.sources[unit.name]
        except KeyError:
            nix_source = nix_rcg_source.create_source(unit.name, 'unit')

        nix_source.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'unit', unit.name)
        Writer.Help.write_metadata(nix_source.metadata, Writer.Help.extract_metadata(unit))

        if recursive:
            Writer.Help.write_many(nix_block, nix_source, unit.spiketrains)

        Writer.Help.clean(nix_block)
        return nix_source

    @staticmethod
    def write_analogsignal(nix_block, signal):
        obj_name = Writer.Help.get_obj_nix_name(signal)

        try:
            nix_array = nix_block.data_arrays[obj_name]

            # TODO update data?

        except KeyError:
            args = (obj_name, 'analogsignal', signal.dtype, (0,1))
            nix_array = nix_block.create_data_array(*args)
            nix_array.append(signal)

        nix_array.unit = signal.units.dimensionality.string

        if not nix_array.dimensions:
            nix_array.append_sampled_dimension(signal.sampling_rate.item())
        nix_array.dimensions[0].unit = signal.sampling_rate.units.dimensionality.string

        metadata = Writer.Help.extract_metadata(signal)

        # special t_start serialization
        metadata['t_start'] = signal.t_start.item()
        metadata['t_start__unit'] = signal.t_start.units.dimensionality.string

        nix_array.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'analogsignal', obj_name)
        Writer.Help.write_metadata(nix_array.metadata, metadata)

        return nix_array

    @staticmethod
    def write_irregularlysampledsignal(nix_block, signal):
        obj_name = Writer.Help.get_obj_nix_name(signal)

        try:
            nix_array = nix_block.data_arrays[obj_name]

            # TODO update data?

        except KeyError:
            args = (obj_name, 'irregularlysampledsignal', signal.dtype, (0,1))
            nix_array = nix_block.create_data_array(*args)
            nix_array.append(signal)

        nix_array.unit = signal.units.dimensionality.string

        if not nix_array.dimensions:
            nix_array.append_range_dimension(np.array(signal.times))  # fix in NIX?
        nix_array.dimensions[0].unit = signal.times.units.dimensionality.string

        metadata = Writer.Help.extract_metadata(signal)

        nix_array.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'irregularlysampledsignal', obj_name)
        Writer.Help.write_metadata(nix_array.metadata, metadata)

        return nix_array

    @staticmethod
    def write_spiketrain(nix_block, st):
        obj_name = Writer.Help.get_obj_nix_name(st)

        try:
            nix_array = nix_block.data_arrays[obj_name]
        except KeyError:
            args = (obj_name, 'spiketrain', st.dtype, (0,))
            nix_array = nix_block.create_data_array(*args)
            nix_array.append(st)

        nix_array.unit = st.units.dimensionality.string

        if st.sampling_rate is not None:
            if not nix_array.dimensions:
                nix_array.append_sampled_dimension(st.sampling_rate.item())
            nix_array.dimensions[0].unit = st.sampling_rate.units.dimensionality.string

        metadata = Writer.Help.extract_metadata(st)

        metadata['t_start'] = st.t_start.item()
        metadata['t_start__unit'] = st.t_start.units.dimensionality.string
        metadata['t_stop'] = st.t_stop.item()
        metadata['t_stop__unit'] = st.t_stop.units.dimensionality.string

        if st.left_sweep:
            metadata['left_sweep'] = st.left_sweep.item()
            metadata['left_sweep__unit'] = st.left_sweep.units.dimensionality.string

        # FIXME waveforms?

        nix_array.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'spiketrain', obj_name)
        Writer.Help.write_metadata(nix_array.metadata, metadata)

        return nix_array

    @staticmethod
    def write_event(nix_block, event):
        obj_name = Writer.Help.get_obj_nix_name(event)

        try:
            nix_array = nix_block.data_arrays[obj_name]
        except KeyError:
            args = (obj_name, 'event', event.times.dtype, (0,))
            nix_array = nix_block.create_data_array(*args)
            nix_array.append(event.times)

        if not nix_array.dimensions:
            nix_array.append_set_dimension()
        nix_array.dimensions[0].labels = event.labels

        metadata = Writer.Help.extract_metadata(event)

        nix_array.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'event', obj_name)
        Writer.Help.write_metadata(nix_array.metadata, metadata)

        return nix_array

    @staticmethod
    def write_epoch(nix_block, epoch):
        obj_name = Writer.Help.get_obj_nix_name(epoch)

        try:
            nix_array = nix_block.data_arrays[obj_name]
        except KeyError:
            data = np.array([epoch.times, epoch.durations])
            args = (obj_name, 'epoch', epoch.times.dtype)
            nix_array = nix_block.create_data_array(*args, data=data)

        if not nix_array.dimensions:
            nix_array.append_set_dimension()
        nix_array.dimensions[0].labels = epoch.labels

        metadata = Writer.Help.extract_metadata(epoch)

        nix_array.metadata = Writer.Help.get_or_create_section(nix_block.metadata, 'epoch', obj_name)
        Writer.Help.write_metadata(nix_array.metadata, metadata)

        return nix_array


class NixIO(BaseIO):
    """
    This I/O can read/write Neo objects into HDF5 format using NIX library.
    """

    is_readable = True
    is_writable = True

    supported_objects = objectlist
    readable_objects = objectlist
    writeable_objects = objectlist

    read_params = dict(zip(objectlist, [] * len(objectlist)))
    write_params = dict(zip(objectlist, [] * len(objectlist)))

    name = 'Nix IO'
    extensions = ['h5']
    mode = 'file'

    def __init__(self, filename, readonly=False):
        """
        Initialize new IO instance.

        If the file does not exist, it will be created.
        This I/O works in a detached mode.

        :param filename: full path to the file (like '/tmp/foo.h5')
        """
        BaseIO.__init__(self, filename=filename)
        self.f = FileHandler(filename)
        self.readonly = readonly

    @file_transaction
    def read_all_blocks(self):
        return [Reader.read_block(self.f, blk.name) for blk in self.f.handle.blocks]

    @file_transaction
    def read_block(self, block_id):
        return Reader.read_block(self.f, block_id)

    @file_transaction
    def write_block(self, block, recursive=True):
        nix_block = Writer.write_block(self.f.handle, block, recursive=recursive)
