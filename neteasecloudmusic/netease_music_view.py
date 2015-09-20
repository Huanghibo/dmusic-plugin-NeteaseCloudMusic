#!/usr/bin/env python
# -*- coding: utf-8 -*-

import gobject
import copy
import time
import random
import os

from dtk.ui.treeview import TreeView
from dtk.ui.threads import post_gui
from dtk.ui.menu import Menu

from widget.ui_utils import render_item_text, draw_single_mask, draw_alpha_mask
from player import Player

import utils
import pango
from xdg_support import get_cache_file
from nls import _
from song import Song
from config import config
from dtk.ui.treeview import TreeItem
from dtk.ui.utils import get_content_size
from dtk.ui.constant import ALIGN_START

from netease_music_player import neteasecloud_music_player as nplayer
from netease_events import event_manager
DEFAULT_FONT_SIZE = 8

class CategoryView(TreeView):
    def add_items(self, items, insert_pos=None, clear_first=False):
        for item in items:
            song_view = getattr(item, "song_view", None)
            if song_view:
                setattr(song_view, "category_view", self)
        TreeView.add_items(self, items, insert_pos, clear_first)

    items = property(lambda self: self.visible_items)

class MusicView(TreeView):
    PLAYING_LIST_TYPE = 1
    FAVORITE_LIST_TYPE = 2
    CREATED_LIST_TYPE = 3
    COLLECTED_LIST_TYPE = 4
    LOGIN_LIST_TYPE = 5
    PERSONAL_FM_ITEM = 6

    LIST_REPEAT = 1
    SINGLE_REPEAT = 2
    ORDER_PLAY = 3
    RANDOMIZE = 4

    FAVORITE_SONGS = []
    CREATED_LISTS_DICT = {}

    __gsignals__ = {
            "begin-add-items" :
                (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
            "empty-items" :
                (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ())
            }

    def __init__(self, data=None, view_type=1):
        TreeView.__init__(self, enable_drag_drop=False,
                enable_multiple_select=True)

        # view_type 为list类型
        self.connect("double-click-item", self.on_music_view_double_click)
        self.connect("press-return", self.on_music_view_press_return)
        self.connect("right-press-items", self.on_music_view_right_press_items)
        #self.connect("delete-select-items",
                #self.on_music_view_delete_select_items)

        self.db_file = get_cache_file("neteasecloudmusic/neteasecloudmusic.db")
        self.view_type = view_type
        self.view_data = data

        self.request_thread_id = 0
        self.collect_thread_id = 0
        self.onlinelist_thread_id = 0
        self.collect_page = 0

        if self.view_type not in [self.PLAYING_LIST_TYPE, self.LOGIN_LIST_TYPE,
                self.PERSONAL_FM_ITEM]:
            self.load_onlinelist_songs()

        if self.view_type == self.PERSONAL_FM_ITEM:
            self.enable_multiple_select=False

    @property
    def items(self):
        return self.get_items()

    @property
    def playback_mode(self):
        return config.get("setting", "loop_mode")

    def on_music_view_double_click(self, widget, item, column, x, y):
        if item:
            song = item.get_song()
            if self.view_type in [self.PLAYING_LIST_TYPE, self.PERSONAL_FM_ITEM]:
                self.set_current_source()
                self.request_song(song, play=True)
            else:
                self.add_play_emit([song])

    def on_music_view_press_return(self, widget, items):
        if items:
            if self.view_type==self.PLAYING_LIST_TYPE:
                song = items[0].get_song()
                self.request_song(song, play=True)
            else:
                songs = [item.get_song() for item in items]
                self.add_play_emit(songs)

    def add_play_emit(self, songs):
        event_manager.emit('add-songs-to-playing-list', (songs, True))

    def add_to_playlist(self, songs):
        self.add_and_play_songs = songs
        event_manager.emit('add-songs-to-playing-list')

    def on_music_view_right_press_items(self, widget, x, y,
            current_item, select_items):
        if current_item and select_items:
            selected_songs_id = [item.get_song()['id'] for item in select_items]
            # 子菜单 - 添加到创建的歌单
            addto_submenu = [(None, _(key),
                self.add_to_list, selected_songs_id,
                self.CREATED_LISTS_DICT[key])
                for key in self.CREATED_LISTS_DICT.keys() if
                self.CREATED_LISTS_DICT[key] != self.list_id]
            if self.view_type != self.PLAYING_LIST_TYPE:
                addto_submenu.insert(0,(None, _('播放列表'),
                    self.add_to_list,selected_songs_id, 0))
            addto_submenu = Menu(addto_submenu)

            # 子菜单 - 从歌单中删除
            delfrom_submenu = [(None, _(item.get_song()['name']), None) for item
                    in select_items]
            delfrom_submenu.insert(0, (None, _('**确认删除以下歌曲**'),
                self.delete_from_list, selected_songs_id, self.list_id))
            delfrom_submenu.insert(len(delfrom_submenu), (None,
                _('**确认删除以上歌曲**'),
                self.delete_from_list, selected_songs_id, self.list_id))
            delfrom_submenu = Menu(delfrom_submenu)

            # 播放列表
            if self.view_type == self.PLAYING_LIST_TYPE:
                if len(select_items) > 1:
                    items = [
                            (None, _("播放"), lambda: self.add_play_emit(
                                [item.get_song() for item in select_items])),
                            (None, _("删除"), lambda:
                                self.delete_from_list(selected_songs_id)),
                            (None, _("清空"), lambda: self.clear_items())
                            ]
                else:
                    items = [
                            (None, _("播放"), lambda:
                                self.add_play_emit([current_item.get_song()])),
                            (None, _("删除"), lambda:
                                self.delete_from_list(selected_songs_id)),
                            (None, _("清空"), lambda: self.clear_items())
                            ]
                items.insert(0, (None, _("添加到"), addto_submenu))
                Menu(items, True).show((int(x), int(y)))

            # 收藏/创建的歌单
            elif self.view_type in [self.FAVORITE_LIST_TYPE,
                    self.COLLECTED_LIST_TYPE, self.CREATED_LIST_TYPE]:
                if len(select_items) > 1:
                    items = [
                            (None, _("播放"), lambda: self.add_play_emit(
                                [item.get_song() for item in select_items])),
                            ]
                else:
                    items = [
                            (None, _("播放"), lambda:
                                self.add_play_emit([current_item.get_song()])),
                            ]
                items.insert(0, (None, _("添加到"), addto_submenu))
                if self.view_type in [self.CREATED_LIST_TYPE,
                        self.FAVORITE_LIST_TYPE]:
                    items.insert(-1, (None, _("删除"), delfrom_submenu))
                Menu(items, True).show((int(x), int(y)))

            # 私人FM
            elif self.view_type == self.PERSONAL_FM_ITEM:
                select_song_name = select_items[0].get_song()['name']
                trash_submenu = Menu([(None, _('确定'), self.fm_trash,
                    current_item)])
                items = [
                        (None, _('删除FM - '+select_song_name), trash_submenu)
                        ]
                if current_item.get_song()['id'] in self.FAVORITE_SONGS:
                    unlike_submenu = Menu([(None, _('确定'), self.fm_like,
                        current_item.get_song(), False)])
                    items.insert(0,
                            (None, _('取消喜欢 - '+select_song_name),
                            unlike_submenu),
                            )
                else:
                    like_submenu = Menu([(None, _('确定'), self.fm_like,
                        current_item.get_song(), True)])
                    items.insert(0,
                            (None, _('喜欢 - '+select_song_name),
                            like_submenu),
                            )
                Menu(items, True).show((int(x), int(y)))

    def delete_playing_list_items(self, items):
        self.delete_items(items)
        event_manager.emit('save-playing-status')

    def add_to_list(self, sids, playlist_id=0):
        if playlist_id and nplayer.add_to_onlinelist(sids, playlist_id):
            event_manager.emit('refresh-online-list', playlist_id)
        elif not playlist_id:
            event_manager.emit('add-songs-to-playing-list', ([song for song in
                self.get_songs() if song['id'] in sids], False))

    def delete_from_list(self, sids, playlist_id=0):
        if playlist_id and nplayer.delete_from_onlinelist(sids, playlist_id):
            event_manager.emit('refresh-online-list', playlist_id)
        elif not playlist_id:
            self.delete_items([item for item in self.items
                if item.get_song()['id'] in sids])
            event_manager.emit('save-playing-status')

    def fm_like(self, song, flag):
        if nplayer.fm_like(song['id'], flag):
            event_manager.emit('refresh-favorite-list')

    def fm_trash(self, current_item):
        if nplayer.fm_trash(current_item.get_song()['id']):
            event_manager.emit('refresh-favorite-list')
            if self.highlight_item == current_item:
                next_song = self.get_next_song()
                self.delete_items([current_item])
                self.request_song(next_song)
            else:
                self.delete_items([current_item])
                self.pre_fetch_fm_songs()

    def get_sids(self, items):
        return ",".join([str(item.song['sid']) for item in items if
            item.song.get('sid', None)])

    def on_music_view_delete_select_items(self, widget, items):
        if not items:
            return

    def clear_items(self):
        self.clear()
        event_manager.emit("save-playing-status")

    def draw_mask(self, cr, x, y, width, height):
        draw_alpha_mask(cr, x, y, width, height, "layoutMiddle")

    # set self as current global playlist
    def set_current_source(self):
        if Player.get_source() != self:
            Player.set_source(self)

    def emit_add_signal(self):
        self.emit("begin-add-items")

    def request_song(self, song, play=True):
        if song:
            self.set_highlight_song(song)
            cover_path = get_cache_file('cover')
            if not os.path.exists(cover_path):
                os.mkdir(cover_path)
            for the_file in os.listdir(cover_path):
                file_path = os.path.join(cover_path, the_file)
                try:
                    os.unlink(file_path)
                except:
                    pass
            song = nplayer.get_better_quality_music(song)
            nplayer.save_lyric(nplayer.get_lyric(song['sid']), song['sid'],
                    song['name'], song['artist'])
            self.play_song(song, play=True)

    def pre_fetch_fm_songs(self):
        if (self.highlight_item and (self.highlight_item in self.items) and
                self.view_type == self.PERSONAL_FM_ITEM):
            current_index = self.items.index(self.highlight_item)
            if current_index >= len(self.items)-2:
                songs = [Song(song) for song in nplayer.personal_fm()]
                songs = [song for song in songs if (song['id'] not in
                    [exists_song['id'] for exists_song in self.get_songs()])]
                if songs:
                    count = len(self.items) + len(songs) - 17
                    if count > 0:
                        self.delete_items([self.items[i] for i in range(count)])
                    self.add_fm(songs)
                else:
                    self.pre_fetch_fm_songs()

    def adjust_uri_expired(self, song):
        expire_time = song.get("uri_expire_time", None)
        duration = song.get("#duration", None)
        fetch_time = song.get("fetch_time", None)
        if not expire_time or not duration or not fetch_time or not song.get("uri", None):
            return True
        now = time.time()
        past_time = now - fetch_time
        if past_time > (expire_time - duration) / 1000 :
            return True
        return False

    def play_song(self, song, play=False):
        if not song: return None

        # update song info
        self.update_songitem(song)

        # clear current select status
        del self.select_rows[:]
        self.queue_draw()

        # set item highlight
        self.set_highlight_song(song)

        if play:
            # play song now
            Player.play_new(song)

            # set self as current global playlist
            self.set_current_source()

            event_manager.emit("save-playing-status")
        self.pre_fetch_fm_songs()
        return song

    @post_gui
    def render_play_song(self, song, play, thread_id):
        if thread_id != self.request_thread_id:
            return

        song["fetch_time"] = time.time()
        self.play_song(Song(song), play)

    def get_songs(self):
        songs = []
        self.update_item_index()
        for song_item in self.items:
            songs.append(song_item.get_song())
        return songs

    def add_fm(self, songs, pos=None, sort=False, play=False):
        song_items = [SongItem(song) for song in songs]
        if song_items:
            if not self.items:
                self.emit_add_signal()
            self.add_items(song_items, pos, False)
            event_manager.emit("save-playing-status")

        if len(songs) >= 1 and play:
            song = songs[0]
            self.request_song(song, play=True)

    def add_songs(self, songs, pos=None, sort=False, play=False):
        if not songs:
            return

        try:
            song_items = [ SongItem(song) for song in songs if song['id'] not in
                    [exists_song['id'] for exists_song in self.get_songs()]]
        except:
            song_items = [ SongItem(Song(song)) for song in songs if song not in
                    [exists_song['id'] for exists_song in self.get_songs()]]

        if song_items:
            if not self.items:
                self.emit_add_signal()
            self.add_items(song_items, pos, False)
            event_manager.emit("save-playing-status")

        if len(songs) >= 1 and play:
            song = songs[0]
            self.request_song(song, play=True)

    def set_highlight_song(self, song):
        if not song: return
        if SongItem(song) in self.items:
            self.set_highlight_item(self.items[self.items.index(SongItem(Song(song)))])
            self.visible_highlight()
            self.queue_draw()

    def update_songitem(self, song):
        if not song: return
        if song in self.items:
            self.items[self.items.index(SongItem(song))].update(song, True)

    def get_next_song(self, maunal=False):
        if len(self.items) <= 0:
            return

        if self.view_type == self.PERSONAL_FM_ITEM:
            self.get_next_fm()
            return

        if self.highlight_item:
            if self.highlight_item in self.items:
                current_index = self.items.index(self.highlight_item)
                if self.playback_mode == 'list_mode':
                    next_index = current_index + 1
                    if next_index > len(self.items) - 1:
                        next_index = 0
                elif self.playback_mode == 'single_mode':
                    next_index = current_index
                elif self.playback_mode == 'order_mode':
                    next_index = current_index + 1
                    if next_index > len(self.items) - 1:
                        return
                elif self.playback_mode == 'random_mode':
                    next_index = random.choice(
                            range(0, current_index)
                            +range(current_index+1, len(self.items)))
                highlight_item = self.items[next_index]
            else:
                highlight_item = self.items[0]
        else:
            highlight_item = self.items[0]
        self.request_song(highlight_item.get_song(), play=True)

    def get_next_fm(self):
        if self.highlight_item and self.highlight_item in self.items:
            current_index = self.items.index(self.highlight_item)
            next_index = current_index + 1
            if next_index > len(self.items) -1:
                return
            highlight_item = self.items[next_index]
        else:
            highlight_item = self.items[0]

        self.request_song(highlight_item.get_song(), play=True)

    def get_previous_song(self):
        if len(self.items) <= 0:
            return

        if self.view_type == self.PERSONAL_FM_ITEM:
            self.get_pervious_fm()
            return

        if self.highlight_item:
            if self.highlight_item in self.items:
                current_index = self.items.index(self.highlight_item)
                if self.playback_mode == 'list_mode':
                    pervious_song = current_index - 1
                    if pervious_song > len(self.items) - 1:
                        pervious_song = 0
                elif self.playback_mode == 'single_mode':
                    pervious_song = current_index
                elif self.playback_mode == 'order_mode':
                    pervious_song = current_index - 1
                    if pervious_song < 0:
                        return
                elif self.playback_mode == 'random_mode':
                    pervious_song = random.choice(
                            range(0, current_index)
                            +range(current_index+1, len(self.items)))
                highlight_item = self.items[pervious_song]
            else:
                highlight_item = self.items[0]
        else:
            highlight_item = self.items[0]

        self.request_song(highlight_item.get_song(), play=True)

    def get_pervious_fm(self):
        if self.highlight_item and self.highlight_item in self.items:
            current_index = self.items.index(self.highlight_item)
            privious_index = current_index - 1
            if privious_index < 0:
                return
            highlight_item = self.items[privious_index]
        else:
            highlight_item = self.items[0]

        self.request_song(highlight_item.get_song(), play=True)

    def dump_songs(self):
        return [ song.get_dict() for song in self.get_songs() ]

    @post_gui
    def render_collect_songs(self, data, thread_id):
        if self.collect_thread_id != thread_id:
            return
        if len(data) == 2:
            songs, havemore = data
            self.add_songs(songs)

    def load_onlinelist_songs(self, clear=True):
        if clear:
            self.clear()

        if not nplayer.is_login:
            return

        if not self.view_data:
            return

        playlist_id = self.list_id

        self.onlinelist_thread_id += 1
        thread_id = copy.deepcopy(self.onlinelist_thread_id)
        utils.ThreadFetch(
            fetch_funcs=(nplayer.playlist_detail, (playlist_id,)),
            success_funcs=(self.render_onlinelist_songs, (thread_id,))
            ).start()

    @post_gui
    def render_onlinelist_songs(self, songs, thread_id):
        if songs and self.view_type == self.FAVORITE_LIST_TYPE:
            event_manager.emit('favorite-list-refreshed', songs)
        if self.onlinelist_thread_id != thread_id:
            return

        if songs:
            self.add_songs([Song(song) for song in songs])

    @property
    def list_id(self):
        if self.view_data:
            try:
                playlist_id = self.view_data.get("id", "")
            except:
                playlist_id = ""
        else:
            playlist_id = ""

        return playlist_id


    @property
    def current_song(self):
        if self.highlight_item:
            return self.highlight_item.get_song()
        return None

class SongItem(TreeItem):
    def __init__(self, song, extend=False):

        TreeItem.__init__(self)

        self.song_error = False
        self.update(song)
        self.extend = extend
        self.height = 26

        self.is_highlight = False
        self.column_index = 0

        self.default_height = 26

    def emit_redraw_request(self):
        if self.redraw_request_callback:
            self.redraw_request_callback(self)

    def update(self, song, redraw=False):
        '''update'''
        self.song = song
        self.title = song.get_str("title")
        self.artist = song.get_str("artist")
        self.length = song.get_str("#duration")
        self.add_time = song.get_str("#added")
        self.album = song.get_str("album")

        # Calculate item size.
        self.title_padding_x = 15
        self.title_padding_y = 5
        (self.title_width, self.title_height) = get_content_size(self.title, DEFAULT_FONT_SIZE)

        self.artist_padding_x = 10
        self.artist_padding_y = 5
        (self.artist_width, self.artist_height) = get_content_size(self.artist, DEFAULT_FONT_SIZE)

        self.length_padding_x = 2
        self.length_padding_y = 5
        (self.length_width, self.length_height) = get_content_size(self.length, DEFAULT_FONT_SIZE)

        self.add_time_padding_x = 10
        self.add_time_padding_y = 5
        (self.add_time_width, self.add_time_height) = get_content_size(self.add_time, DEFAULT_FONT_SIZE)

        self.album_padding_x = 10
        self.album_padding_y = 5
        (self.album_width, self.album_height) = get_content_size(self.album, DEFAULT_FONT_SIZE)

        if redraw:
            self.emit_redraw_request()

    def set_error(self):
        if not self.song_error:
            self.song_error = True
            self.emit_redraw_request()

    def clear_error(self):
        if self.song_error:
            self.song_error = False
            self.emit_redraw_request()

    def exists(self):
        return self.song.exists()

    def is_error(self):
        return self.song_error == True

    def render_title(self, cr, rect):
        '''Render title.'''
        if self.is_highlight:
            draw_single_mask(cr, rect.x + 1, rect.y, rect.width, rect.height, "globalItemHighlight")
        elif self.is_select:
            draw_single_mask(cr, rect.x + 1, rect.y, rect.width, rect.height, "globalItemSelect")
        elif self.is_hover:
            draw_single_mask(cr, rect.x + 1, rect.y, rect.width, rect.height, "globalItemHover")

        # if self.is_highlight:
        #     text_color = "#ffffff"
        # else:
        #     text_color = app_theme.get_color("labelText").get_color()

        rect.x += self.title_padding_x
        rect.width -= self.title_padding_x * 2
        render_item_text(cr, self.title, rect, self.is_select, self.is_highlight, error=self.song_error)

    def render_artist(self, cr, rect):
        '''Render artist.'''
        if self.is_highlight:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHighlight")
        elif self.is_select:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemSelect")
        elif self.is_hover:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHover")


        rect.x += self.artist_padding_x
        rect.width -= self.artist_padding_x * 2
        render_item_text(cr, self.artist, rect, self.is_select,
                self.is_highlight, error=self.song_error,
                align=pango.ALIGN_RIGHT)

    def render_length(self, cr, rect):
        '''Render length.'''
        if self.is_highlight:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHighlight")
        elif self.is_select:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemSelect")
        elif self.is_hover:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHover")


        rect.width -= self.length_padding_x * 2
        rect.x += self.length_padding_x * 2
        render_item_text(cr, self.length, rect, self.is_select, self.is_highlight, error=self.song_error, font_size=8)

    def render_add_time(self, cr, rect):
        '''Render add_time.'''
        if self.is_highlight:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHighlight")
        elif self.is_select:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemSelect")
        elif self.is_hover:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHover")

        rect.width -= self.add_time_padding_x * 2
        render_item_text(cr, self.add_time, rect, self.is_select, self.is_highlight, align=ALIGN_START,
                         error=self.song_error, font_size=8)

    def render_album(self, cr, rect):
        '''Render album.'''
        if self.is_highlight:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHighlight")
        elif self.is_select:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemSelect")
        elif self.is_hover:
            draw_single_mask(cr, rect.x, rect.y, rect.width, rect.height, "globalItemHover")

        rect.width -= self.album_padding_x * 2
        render_item_text(cr, self.album, rect, self.is_select, self.is_highlight, error=self.song_error)

    def get_height(self):
        # if self.is_highlight:
        #     return 32
        return self.default_height

    def get_column_widths(self):
        '''Get sizes.'''
        if self.extend:
            return (100, 100, 100, 90)
        else:
            return (156, 51, 102)


    def get_column_renders(self):
        '''Get render callbacks.'''

        if self.extend:
            return (self.render_title, self.render_artist, self.render_album, self.render_add_time)
        else:
            return (self.render_title, self.render_length, self.render_artist)

    def unselect(self):
        self.is_select = False
        self.emit_redraw_request()

    def select(self):
        self.is_select = True
        self.emit_redraw_request()

    def highlight(self):
        self.is_highlight = True
        self.is_select = False
        self.emit_redraw_request()

    def unhighlight(self):
        self.is_highlight = False
        self.is_select = False
        self.emit_redraw_request()

    def unhover(self, column, offset_x, offset_y):
        self.is_hover = False
        self.emit_redraw_request()

    def hover(self, column, offset_x, offset_y):
        self.is_hover = True
        self.emit_redraw_request()

    def get_song(self):
        return self.song

    def __hash__(self):
        return hash(self.song.get("uri"))

    def __repr__(self):
        return "<SongItem %s>" % self.song.get("uri")

    def __cmp__(self, other_item):
        if not other_item:
            return -1
        try:
            return cmp(self.song, other_item.get_song())
        except AttributeError: return -1

    def __eq__(self, other_item):
        try:
            return self.song == other_item.get_song()
        except:
            return False
