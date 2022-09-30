#! /usr/bin/env python3

from __future__ import print_function

__docformat__ = 'restructuredtext'
__version__ = '$Id: $'

import os
import sys
import weakref
import argparse

from pyglet.gl import *
import pyglet
from pyglet.window import key

import json
from pathlib import Path

import io
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


pyglet.options['debug_media'] = False
# pyglet.options['audio'] = ('openal', 'pulse', 'silent')
from pyglet.media import buffered_logger as bl


def draw_rect(x, y, width, height, color=(192, 192, 192, 192)):
    pyglet.graphics.draw(
        4,
        GL_LINE_LOOP,
        ('v3f', (x, y, 0,
                        x + width, y, 0,
                        x + width, y + height, 0,
                        x, y + height, 0,
                        )
        ),
        ('c4B', color * 4)
    )


class Control(pyglet.event.EventDispatcher):
    x = y = 0
    width = height = 10

    def __init__(self, parent):
        super(Control, self).__init__()
        self.parent = weakref.proxy(parent)

    def hit_test(self, x, y):
        return (self.x < x < self.x + self.width and
                self.y < y < self.y + self.height)

    def capture_events(self):
        self.parent.push_handlers(self)

    def release_events(self):
        self.parent.remove_handlers(self)


class Button(Control):
    charged = False

    def draw(self):
        if self.charged:
            draw_rect(self.x, self.y, self.width, self.height)
        else:
            draw_rect(self.x, self.y, self.width, self.height, color=(192, 0, 0, 192))
        self.draw_label()

    def on_mouse_press(self, x, y, button, modifiers):
        self.capture_events()
        self.charged = True

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.charged = self.hit_test(x, y)

    def on_mouse_release(self, x, y, button, modifiers):
        self.release_events()
        if self.hit_test(x, y):
            self.dispatch_event('on_press')
        self.charged = False


Button.register_event_type('on_press')


class TextButton(Button):
    def __init__(self, *args, **kwargs):
        super(TextButton, self).__init__(*args, **kwargs)
        self._text = pyglet.text.Label('', anchor_x='center', anchor_y='center')

    def draw_label(self):
        self._text.x = self.x + self.width / 2
        self._text.y = self.y + self.height / 2
        self._text.draw()

    def set_text(self, text):
        self._text.text = text

    text = property(lambda self: self._text.text,
                    set_text)


class Slider(Control):
    THUMB_WIDTH = 6
    THUMB_HEIGHT = 10
    GROOVE_HEIGHT = 2
    RESPONSIVNESS = 0.3

    def __init__(self, *args, **kwargs):
        super(Slider, self).__init__(*args, **kwargs)
        self.seek_value = None

    def draw(self):
        center_y = self.y + self.height / 2
        draw_rect(self.x, center_y - self.GROOVE_HEIGHT / 2,
                  self.width, self.GROOVE_HEIGHT)
        pos = self.x + self.value * self.width / (self.max - self.min)
        draw_rect(pos - self.THUMB_WIDTH / 2, center_y - self.THUMB_HEIGHT / 2,
                  self.THUMB_WIDTH, self.THUMB_HEIGHT)

    def coordinate_to_value(self, x):
        value = float(x - self.x) / self.width * (self.max - self.min) + self.min
        return value

    def on_mouse_press(self, x, y, button, modifiers):
        value = self.coordinate_to_value(x)
        self.capture_events()
        self.dispatch_event('on_begin_scroll')
        self.dispatch_event('on_change', value)
        pyglet.clock.schedule_once(self.seek_request, self.RESPONSIVNESS)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        # On some platforms, on_mouse_drag is triggered with a high frequency.
        # Seeking takes some time (~200ms). Asking for a seek at every 
        # on_mouse_drag event would starve the event loop. 
        # Instead we only record the last mouse position and we
        # schedule seek_request to dispatch the on_change event in the future.
        # This will allow subsequent on_mouse_drag to change the seek_value
        # without triggering yet the on_change event.
        value = min(max(self.coordinate_to_value(x), self.min), self.max)
        if self.seek_value is None:
            # We have processed the last recorded mouse position.
            # We re-schedule seek_request
            pyglet.clock.schedule_once(self.seek_request, self.RESPONSIVNESS)
        self.seek_value = value

    def on_mouse_release(self, x, y, button, modifiers):
        self.release_events()
        self.dispatch_event('on_end_scroll')
        self.seek_value = None

    def seek_request(self, dt):
        if self.seek_value is not None:
            self.dispatch_event('on_change', self.seek_value)
            self.seek_value = None


Slider.register_event_type('on_begin_scroll')
Slider.register_event_type('on_end_scroll')
Slider.register_event_type('on_change')


class PlayerWindow(pyglet.window.Window):
    GUI_WIDTH = 1000
    GUI_HEIGHT = 400
    GUI_PADDING = 4
    GUI_BUTTON_HEIGHT = 16

    def __init__(self, player, dir, clips):
        super(PlayerWindow, self).__init__(caption='Mixaqubes',
                                           visible=False,
                                           resizable=True)
        self.dir = dir
        # We only keep a weakref to player as we are about to push ourself
        # as a handler which would then create a circular reference between
        # player and window.
        self.player = weakref.proxy(player)
        self._player_playing = False
        self.player.push_handlers(self)

        self.slider = Slider(self)
        self.slider.push_handlers(self)
        self.slider.x = self.GUI_PADDING
        self.slider.y = self.GUI_PADDING * 2 + self.GUI_BUTTON_HEIGHT

        self.play_pause_button = TextButton(self)
        self.play_pause_button.x = self.GUI_PADDING
        self.play_pause_button.y = self.GUI_PADDING
        self.play_pause_button.height = self.GUI_BUTTON_HEIGHT
        self.play_pause_button.width = 45
        self.play_pause_button.on_press = self.on_play_pause

        self.window_button = TextButton(self)
        self.window_button.x = self.play_pause_button.x + \
                               self.play_pause_button.width + self.GUI_PADDING
        self.window_button.y = self.GUI_PADDING
        self.window_button.height = self.GUI_BUTTON_HEIGHT
        self.window_button.width = 90
        self.window_button.text = 'Windowed'
        self.window_button.on_press = lambda: self.set_fullscreen(False)

        self.controls = [
            self.slider,
            self.play_pause_button,
            self.window_button,
        ]

        x = self.window_button.x + self.window_button.width + self.GUI_PADDING
        # i = 0
        # for screen in self.display.get_screens():
        #     screen_button = TextButton(self)
        #     screen_button.x = x
        #     screen_button.y = self.GUI_PADDING
        #     screen_button.height = self.GUI_BUTTON_HEIGHT
        #     screen_button.width = 80
        #     screen_button.text = 'Screen %d' % (i + 1)
        #     screen_button.on_press = lambda screen=screen: self.set_fullscreen(True, screen)
        #     self.controls.append(screen_button)
        #     i += 1
        #     x += screen_button.width + self.GUI_PADDING

        self.clips = clips
        self.clip_names = list(clips.keys())
        self.active_clip = []
        self.active_bar = 0
        self.active_bpm = None
        self.active_key = None

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    def on_player_next_source(self):
        self.gui_update_state()
        self.gui_update_source()
        self.set_default_video_size()
        return True

    def on_player_eos(self):
        self.gui_update_state()
        # pyglet.clock.schedule_once(self.auto_close, 0.1)
        if len(self.active_clip) != 0:
            self.player.queue(self.active_clip[self.active_bar])
            self.active_bar = (self.active_bar + 1) % len(self.active_clip)
            print("playing bar {}".format(self.active_bar))
            self.player.play()
            self.gui_update_source()
        return True

    def gui_update_source(self):
        if self.player.source:
            source = self.player.source
            self.slider.min = 0.
            self.slider.max = source.duration
        else:
            self.slider.min = 0.
            self.slider.max = 100.
        self.gui_update_state()

    def gui_update_state(self):
        if self.player.playing:
            self.play_pause_button.text = 'Pause'
        else:
            self.play_pause_button.text = 'Play'

    def get_video_size(self):
        if not self.player.source or not self.player.source.video_format:
            return 0, 0
        video_format = self.player.source.video_format
        width = video_format.width
        height = video_format.height
        if video_format.sample_aspect > 1:
            width *= video_format.sample_aspect
        elif video_format.sample_aspect < 1:
            height /= video_format.sample_aspect
        return width, height

    def set_default_video_size(self):
        """Make the window size just big enough to show the current
        video and the GUI."""
        width = self.GUI_WIDTH
        height = self.GUI_HEIGHT
        video_width, video_height = self.get_video_size()
        width = max(width, video_width)
        height += video_height
        self.set_size(int(width), int(height))

    def on_resize(self, width, height):
        """Position and size video image."""
        super(PlayerWindow, self).on_resize(width, height)
        self.slider.width = width - self.GUI_PADDING * 2

        height -= self.GUI_HEIGHT
        if height <= 0:
            return

        video_width, video_height = self.get_video_size()
        if video_width == 0 or video_height == 0:
            return
        display_aspect = width / float(height)
        video_aspect = video_width / float(video_height)
        if video_aspect > display_aspect:
            self.video_width = width
            self.video_height = width / video_aspect
        else:
            self.video_height = height
            self.video_width = height * video_aspect
        self.video_x = (width - self.video_width) / 2
        self.video_y = (height - self.video_height) / 2 + self.GUI_HEIGHT

    def on_mouse_press(self, x, y, button, modifiers):
        for control in self.controls:
            if control.hit_test(x, y):
                control.on_mouse_press(x, y, button, modifiers)

    def cache_active_clip(self, name, loop):
        meta = self.clips[name]["loops"][loop]
        if meta is not None:
            media_path = Path(self.dir + "/" + name + "/" + loop + ".wav")
            if media_path.exists():
                loop = pyglet.media.load(media_path)
                sample_rate = loop.audio_format.sample_rate
                print("sample rate: {}".format(sample_rate))

                bpm = float(self.clips[name]["bpm"])
                print("bpm: {}".format(bpm))
                self.active_bpm = bpm
                self.active_key = self.clips[name]["key"]

                samples_per_beat = round((60.0 / bpm) * sample_rate)
                print("samples per beat: {}".format(samples_per_beat))
                bytes_per_sample = loop.audio_format.channels * loop.audio_format.sample_size // 8
                print("bytes per beat: {}".format(samples_per_beat * bytes_per_sample))
                beats = int(meta["beats"])
                bytes_per_bar = 4 * samples_per_beat * bytes_per_sample

                full_loop = loop.get_audio_data(int(samples_per_beat * bytes_per_sample * beats))
                print("full loop len: ", len(full_loop.data))
                bars_raw = [full_loop.data[i:i+bytes_per_bar] for i in range(0, len(full_loop.data), bytes_per_bar)]
                self.active_clip = []
                for bar_raw in bars_raw:
                    print("slicing bar of {} bytes".format(len(bar_raw)))
                    self.active_clip += [
                        MemorySource(bar_raw, loop.audio_format)
                    ]
                self.active_bar = 0

                # one_bar = loop.get_audio_data(int(samples_per_bar * bytes_per_sample))
                # print("one bar bytes: {}".format(one_bar.length))

                # self.active_clip = MemorySource(one_bar.data, loop.audio_format)
                # 1498560 bytes -> 93,660 bytes/beat -> 374640 bytes/bar
                return (name, loop)
            else:
                print("invalid media path: {}".format(media_path))
                exit(0)
        else:
            print("Not in database: {}/{}".format(name, loop))
        return None

    def on_key_press(self, symbol, modifiers):
        if symbol == key.SPACE:
            self.on_play_pause()
        elif symbol == key.ESCAPE:
            self.dispatch_event('on_close')
        elif symbol == key.LEFT:
            self.player.seek(0)
        elif symbol == key.RIGHT:
            self.player.next_source()
        elif symbol == key._1:
            print("got 1")
            (name, loop) = self.cache_active_clip(self.clip_names[0], "basic")
            print("set active clip to {}/{}".format(name, loop))
        elif symbol == key._2:
            print("got 2")
            (name, loop) = self.cache_active_clip(self.clip_names[0], "pre-drop")
            print("set active clip to {}/{}".format(name, loop))
        elif symbol == key._3:
            print("got 3")
            (name, loop) = self.cache_active_clip(self.clip_names[0], "drop")
            print("set active clip to {}/{}".format(name, loop))
        elif symbol == key._4:
            print("got 4")
            (name, loop) = self.cache_active_clip(self.clip_names[1], "intro")
            print("set active clip to {}/{}".format(name, loop))
        elif symbol == key._5:
            print("got 5")
            (name, loop) = self.cache_active_clip(self.clip_names[1], "intro2")
            print("set active clip to {}/{}".format(name, loop))
        elif symbol == key._6:
            print("got 6")
            (name, loop) = self.cache_active_clip(self.clip_names[1], "mid")
            print("set active clip to {}/{}".format(name, loop))
        if self.player.playing == False and len(self.active_clip) != 0:
            print("bootstrapping play queue")
            self.player.queue(self.active_clip[self.active_bar])
            self.active_bar = (self.active_bar + 1) % len(self.active_clip)
            self.player.play()
            self.gui_update_source()

    def on_close(self):
        self.player.pause()
        self.close()

    def auto_close(self, dt):
        self.close()

    def on_play_pause(self):
        if self.player.playing:
            self.player.pause()
        else:
            if self.player.time >= self.player.source.duration:
                self.player.seek(0)
            self.player.play()
        self.gui_update_state()

    def on_draw(self):
        self.clear()

        # Video
        if self.player.source and self.player.source.video_format:
            video_texture = self.player.texture
            video_texture.blit(self.video_x,
                               self.video_y,
                               width=self.video_width,
                               height=self.video_height)

        # GUI
        self.slider.value = self.player.time
        for control in self.controls:
            control.draw()

    def on_begin_scroll(self):
        self._player_playing = self.player.playing
        self.player.pause()

    def on_change(self, value):
        self.player.seek(value)

    def on_end_scroll(self):
        if self._player_playing:
            self.player.play()


def main():
    parser = argparse.ArgumentParser(description="Mixaqubes demo 1", prog="python3 -m mixaqubes")
    parser.add_argument(
        "--debug", required=False, help="turn on debugging", action="store_true"
    )
    parser.add_argument(
        "--outfile", required=False, help="debug capture file", type=str
    )
    parser.add_argument(
        "--directory", required=False, help="directory of clips", type=str, default="clips"
    )
    args = parser.parse_args()
    debug = args.debug
    dbg_file = args.outfile

    set_logging_parameters(dbg_file, debug)

    with open(args.directory + "/manifest.json") as manifest:
        clips = json.load(manifest)

    #print(clips)
    #for filename in args.file:
    #    clips += [pyglet.media.load(filename)]

    player = pyglet.media.Player()
    window = PlayerWindow(player, args.directory, clips)

    # player.queue(pyglet.media.load(filename) for filename in args.file)

    window.gui_update_source()
    window.set_visible(True)
    window.set_default_video_size()

    # this is an async call
    player.pause()
    window.gui_update_state()

    pyglet.app.run()


def set_logging_parameters(dbg_file, debug):
    if not debug:
        bl.logger = None
        return

    dbg_dir = os.path.dirname(dbg_file)
    if dbg_dir and not os.path.isdir(dbg_dir):
        os.mkdir(dbg_dir)

    bl.logger = bl.BufferedLogger(dbg_file)
    from pyglet.media.instrumentation import mp_events
    # allow to detect crashes by prewriting a crash file, if no crash
    # it will be overwrited by the captured data
    sample = os.path.basename(dbg_file)
    bl.logger.log("version", mp_events["version"])
    bl.logger.log("crash", sample)
    bl.logger.save_log_entries_as_pickle()
    bl.logger.clear()
    # start the real capture data
    bl.logger.log("version", mp_events["version"])
    bl.logger.log("mp.im", sample)

if __name__ == "__main__":
    main()
    exit(0)
