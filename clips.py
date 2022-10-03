import pyglet
import io
import torch
import numpy as np

DEVICE="cuda" if torch.cuda.is_available() else "cpu"

class MemorySource(pyglet.media.StaticSource):
    """
    Helper class for default implementation of :class:`.StaticSource`.

    Do not use directly. This class is used internally by pyglet.

    Args:
        data (AudioData): The audio data.
        audio_format (AudioFormat): The audio format.
    """

    def __init__(self, data, audio_format):
        """Construct a memory source over the given data buffer."""
        self._file = io.BytesIO(data)
        self._data = self._file.getvalue()
        self._max_offset = len(data)
        self.audio_format = audio_format
        self._duration = len(data) / float(audio_format.bytes_per_second)

    def seek(self, timestamp):
        """Seek to given timestamp.

        Args:
            timestamp (float): Time where to seek in the source.
        """
        offset = int(timestamp * self.audio_format.bytes_per_second)

        # Align to sample
        if self.audio_format.bytes_per_sample == 2:
            offset &= 0xfffffffe
        elif self.audio_format.bytes_per_sample == 4:
            offset &= 0xfffffffc

        self._file.seek(offset)

# A `Clip` contains a list of audio data, stored in `bars`,
# where each element of the list is exactly one bar of audio data
#
# The metadata of the clip is as follows:
#   - name: string containing the name of the song
#   - element: string describing the thematic element (intro, drop, outro, etc.)
#   - bars: a list of "bar" audio data, where each bar is a `MemorySource` audio source
#   - bpm: a float that is the beats per minute of the clip
#   - key: a string that describes the Camelot mixing key of the clip
class Clip():
    def __init__(self, name, element, bars, bpm, key):
        self.name = name
        self.element = element
        self.bars = bars
        self.bpm = bpm
        self.key = key
        self.bar = 0

        # this controls the magnitude of the current effect
        self.magnitude = 1.0
        # this is a list of effects to be applied
        self.effects = [FadeIn()]
        # this is the current state of the clip
        self.state = "cued"

    # this function can take some time to complete, so, call any next_bar() calls and get your clips first
    # then play them all at once.
    def next_bar(self):
        current_bar = self.bar
        print("computing bar {} of {}".format(current_bar, self.name))

        # apply any effects via the pytorch pipeline
        torchdata = self.to_torch(self.bars[current_bar])
        if len(self.effects) > 0:
            torchdata = self.effects[0].process(torchdata)
            if self.effects[0].is_done():
                self.effects = self.effects[1:]
                # TODO: something with self.state here
        self.current_bar = self.from_torch(torchdata)

        # advance the bar count
        self.bar = (self.bar + 1) % len(self.bars)
        return self.current_bar

    # mem_source is a MemorySource
    def to_torch(self, mem_source):
        self.audio_format = mem_source.audio_format
        raw_ints = np.frombuffer(mem_source._data, dtype=np.int16)
        a = raw_ints[::2]
        b = raw_ints[1::2]
        deinterleaved = np.stack((a,b))
        sample = torch.tensor(
            deinterleaved,
            dtype=torch.float32,
            device=DEVICE,
        )
        return sample

    # returns a MemorySource from pytorch data
    def from_torch(self, torch_audio):
        stereo_channels = torch_audio.cpu().numpy()
        stream = np.empty((stereo_channels[0].size + stereo_channels[1].size,), dtype=stereo_channels.dtype)
        stream[0::2] = stereo_channels[0]
        stream[1::2] = stereo_channels[1]
        int_stream = stream.astype(np.int16)
        return MemorySource(int_stream.tobytes(), self.audio_format)

class FadeIn():
    def __init__(self):
        self.gain = -30.0
        self.increment = 10.0
    def step(self):
        self.gain += self.increment
        if self.gain > 0.0:
            self.gain = 0.0
    def is_done(self):
        if self.gain >= 0.0:
            return True
        else:
            return False
    def process(self, torchdata):
        start_ratio = 10 ** (self.gain / 20)
        end_gain = self.gain + self.increment
        if end_gain > 0.0:
            end_gain = 0.0
        end_ratio = 10 ** (end_gain / 20)

        fade = torch.linspace(start_ratio, end_ratio, torchdata.size()[-1], device=DEVICE)

        proc = torchdata * fade

        self.step()
        return proc