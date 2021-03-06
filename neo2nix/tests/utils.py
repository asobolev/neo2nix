from neo import *
from neo.test.generate_datasets import generate_one_simple_block


def build_fake_block():
    objects = [Block, Segment, RecordingChannelGroup,
               Event, Epoch, Unit, AnalogSignal, SpikeTrain]

    b = generate_one_simple_block(supported_objects=objects)
    b.name = 'foo'
    b.description = 'this is a test block'
    b.file_origin = '/tmp/nonexist.ing'

    b.annotations = {
        'string': 'hello, world!',
        'int': 42,
        'float': 42.0,
        'bool': True
    }

    # FIXME remove this when Neo is fixed (RCG - Unit branch)
    irr1 = IrregularlySampledSignal([5, 6, 7], [2, 3, 4], 'mV', 'ms')
    irr2 = IrregularlySampledSignal([7, 8, 9], [1, 5, 9], 'mV', 'ms')
    irr3 = IrregularlySampledSignal([0, 1, 2], [2, 5, 1], 'mV', 'ms')

    b.segments[0].irregularlysampledsignals.append(irr1)
    b.segments[0].irregularlysampledsignals.append(irr2)
    b.segments[1].irregularlysampledsignals.append(irr3)

    inds = [x for x in range(10)]
    names = ['foo' + str(x) for x in inds]

    unit1 = Unit('unit1')
    unit2 = Unit('unit2')
    unit3 = Unit('unit3')

    for st in b.segments[0].spiketrains:
        unit1.spiketrains.append(st)

    for st in b.segments[1].spiketrains:
        unit2.spiketrains.append(st)

    rcg1 = RecordingChannelGroup(name='rcg1', channel_indexes=inds, channel_names=names)
    rcg2 = RecordingChannelGroup(name='rcg2', channel_indexes=inds, channel_names=names)

    rcg1.units.append(unit1)
    rcg1.units.append(unit2)
    rcg2.units.append(unit3)

    for sig in b.segments[0].analogsignals:
        rcg1.analogsignals.append(sig)

    for sig in b.segments[1].analogsignals:
        rcg2.analogsignals.append(sig)

    for sig in b.segments[0].irregularlysampledsignals:
        rcg1.irregularlysampledsignals.append(sig)

    for sig in b.segments[1].irregularlysampledsignals:
        rcg2.irregularlysampledsignals.append(sig)

    b.recordingchannelgroups = [rcg1, rcg2]

    return b