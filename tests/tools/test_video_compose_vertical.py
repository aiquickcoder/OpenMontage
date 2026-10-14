"""Vertical episodes: true-pixel ASS subtitles, dissolve/dip transitions, Remotion frame size."""

from pathlib import Path

from tools.video.video_compose import VideoCompose

SRT = """1
00:00:03,500 --> 00:00:07,500
Да, буду. Жена с ребёнком останется. И ещё очень длинная реплика для проверки переноса строк.

2
00:00:08,500 --> 00:00:11,000
А собеседование?
"""


def test_srt_becomes_ass_at_real_frame_size(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text(SRT, encoding="utf-8")
    flt = VideoCompose()._subtitle_filter(srt, {"font_size": 36, "background": "#00000066"}, 1080, 1920, tmp_path)
    ass = (tmp_path / "s.1080x1920.ass").read_text(encoding="utf-8")
    assert flt.startswith("subtitles='") and "s.1080x1920.ass" in flt
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    style = next(line for line in ass.splitlines() if line.startswith("Style: Default"))
    fields = style.split(",")
    assert 42 <= int(fields[2]) <= 86          # 36 is clamped up to a readable size, never ~240 px
    assert fields[15] == "1"                    # outline, no opaque box unless border_style=3
    assert int(fields[-2]) >= 1920 * 0.14       # above the platform UI dead zone
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events[0].startswith("Dialogue: 0,0:00:03.50,0:00:07.50")
    assert events[0].count("\\N") == 1          # at most two lines
    assert events[1].endswith("А собеседование?")


def test_boxed_style_only_when_asked(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text(SRT, encoding="utf-8")
    VideoCompose()._subtitle_filter(srt, {"border_style": 3}, 1080, 1920, tmp_path)
    style = next(l for l in (tmp_path / "s.1080x1920.ass").read_text().splitlines() if l.startswith("Style:"))
    assert style.split(",")[15] == "3"


def test_cut_transitions():
    cuts = [{"transition_out": "cut"}, {"transition_in": "dissolve", "transition_duration": 0.4},
            {"transition_in": "dip_to_black"}, {}]
    assert VideoCompose._cut_transitions(cuts) == [("fade", 0.4), ("fadeblack", 0.5), ("cut", 0.3)]
    assert VideoCompose._cut_transitions([{}, {}]) == [("cut", 0.3)]


def test_remotion_frame_from_edit(tmp_path):
    vc = VideoCompose()
    assert vc._frame_from_edit({"metadata": {"compose_target": {"width": 1080, "height": 1920}}}, {}) == (1080, 1920)
    assert vc._frame_from_edit({"aspect_ratio": "9:16"}, {}) == (1080, 1920)
    assert vc._frame_from_edit({}, {}) is None
