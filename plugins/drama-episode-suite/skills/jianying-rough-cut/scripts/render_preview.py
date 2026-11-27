#!/usr/bin/env python3
"""Render a review MP4 from a v4/v5 timeline, preserving per-segment source audio roles."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


def quote_filter(value: str) -> str:
    return value.replace("'", r"'\''")


def atempo_chain(speed: float) -> str:
    values = []
    remaining = speed
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        values.append(2.0)
        remaining /= 2.0
    values.append(remaining)
    return ",".join(f"atempo={value:.9f}" for value in values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--subtitles", type=Path, required=True)
    parser.add_argument("--subtitle-ass", type=Path, help="Optional ASS preview with exact requested position and shadow")
    parser.add_argument("--music", type=Path, help="Prepared user-supplied music covering the timeline")
    parser.add_argument("--music-gain-db", type=float, default=-6.0)
    parser.add_argument("--sfx-manifest", type=Path, help="JSON list of additive sound effects")
    parser.add_argument("--font-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--subtitle-size", type=float, default=56.0)
    parser.add_argument("--subtitle-margin-v", type=int, default=1360)
    args = parser.parse_args()

    for path in (args.timeline, args.voice, args.subtitles, args.ffmpeg):
        if not path.exists():
            parser.error(f"Input does not exist: {path}")
    for path in (args.subtitle_ass, args.music, args.sfx_manifest):
        if path is not None and not path.is_file():
            parser.error(f"Input does not exist: {path}")
    if args.output.exists():
        parser.error(f"Refusing to overwrite existing preview: {args.output}")
    timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
    segments = timeline.get("segments", [])
    voice_regions = timeline.get("voice_regions", [])
    final_duration = float(timeline.get("timeline_duration", timeline.get("final_duration", 0.0)))
    effects = json.loads(args.sfx_manifest.read_text(encoding="utf-8")) if args.sfx_manifest else []
    if not isinstance(effects, list):
        parser.error("SFX manifest must be a JSON list")
    for effect in effects:
        if not Path(effect["path"]).is_file():
            parser.error(f"Sound effect does not exist: {effect['path']}")
    if not segments or not voice_regions or final_duration <= 0:
        parser.error("Timeline needs video segments, retained voice regions, and a positive duration")

    files = []
    for segment in segments:
        path = str(Path(segment["video_file"]).resolve())
        if path not in files:
            files.append(path)
    file_input = {path: index for index, path in enumerate(files)}
    voice_input = len(files)
    video_counts = Counter(str(Path(item["video_file"]).resolve()) for item in segments)
    audio_counts = Counter(
        str(Path(item["video_file"]).resolve())
        for item in segments if float(item.get("source_audio_volume", 0.0)) > 0
    )
    video_labels = defaultdict(list)
    audio_labels = defaultdict(list)
    filters = []
    for path in files:
        input_index = file_input[path]
        vlabels = [f"vsrc{input_index}_{index}" for index in range(video_counts[path])]
        video_labels[path].extend(vlabels)
        if len(vlabels) == 1:
            filters.append(f"[{input_index}:v]null[{vlabels[0]}]")
        else:
            filters.append(
                f"[{input_index}:v]split={len(vlabels)}" + "".join(f"[{label}]" for label in vlabels)
            )
        alabels = [f"asrc{input_index}_{index}" for index in range(audio_counts[path])]
        audio_labels[path].extend(alabels)
        if len(alabels) == 1:
            filters.append(f"[{input_index}:a]anull[{alabels[0]}]")
        elif alabels:
            filters.append(
                f"[{input_index}:a]asplit={len(alabels)}" + "".join(f"[{label}]" for label in alabels)
            )

    vposition = defaultdict(int)
    aposition = defaultdict(int)
    concat_parts = []
    for index, segment in enumerate(segments):
        path = str(Path(segment["video_file"]).resolve())
        vlabel = video_labels[path][vposition[path]]
        vposition[path] += 1
        source_start = float(segment["source_start"])
        source_end = float(segment["source_end"])
        speed = float(segment["speed"])
        target_duration = float(segment["timeline_end"]) - float(segment["timeline_start"])
        filters.append(
            f"[{vlabel}]trim=start={source_start:.6f}:end={source_end:.6f},"
            f"setpts=(PTS-STARTPTS)/{speed:.9f},"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={args.fps},setsar=1,format=yuv420p[v{index}]"
        )
        volume = float(segment.get("source_audio_volume", 0.0))
        if volume > 0:
            alabel = audio_labels[path][aposition[path]]
            aposition[path] += 1
            filters.append(
                f"[{alabel}]atrim=start={source_start:.6f}:end={source_end:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"{atempo_chain(speed)},volume={volume:.6f},"
                "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad=pad_dur={target_duration:.6f},atrim=duration={target_duration:.6f}[a{index}]"
            )
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={target_duration:.6f}[a{index}]")
        concat_parts.extend([f"[v{index}]", f"[a{index}]"])
    filters.append("".join(concat_parts) + f"concat=n={len(segments)}:v=1:a=1[vbase][abase]")

    voice_labels = [f"voicesrc{index}" for index in range(len(voice_regions))]
    if len(voice_labels) == 1:
        filters.append(f"[{voice_input}:a]anull[{voice_labels[0]}]")
    else:
        filters.append(
            f"[{voice_input}:a]asplit={len(voice_labels)}"
            + "".join(f"[{label}]" for label in voice_labels)
        )
    delayed = []
    for index, (label, region) in enumerate(zip(voice_labels, voice_regions)):
        delay_ms = round(float(region["timeline_start"]) * 1000)
        output_label = f"voice{index}"
        delayed.append(output_label)
        filters.append(
            f"[{label}]atrim=start={float(region['source_start']):.6f}:"
            f"end={float(region['source_end']):.6f},asetpts=PTS-STARTPTS,"
            "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"adelay={delay_ms}:all=1[{output_label}]"
        )
    filters.append(f"anullsrc=r=48000:cl=stereo:d={final_duration:.6f}[voicesilence]")
    filters.append(
        "[voicesilence]" + "".join(f"[{label}]" for label in delayed)
        + f"amix=inputs={len(delayed) + 1}:normalize=0:dropout_transition=0[voicefull]"
    )
    mix_labels = ["abase", "voicefull"]
    next_input = voice_input + 1
    if args.music is not None:
        filters.append(
            f"[{next_input}:a]aresample=48000,volume={10 ** (args.music_gain_db / 20):.8f},"
            f"apad,atrim=duration={final_duration:.6f}[musicfull]"
        )
        mix_labels.append("musicfull")
        next_input += 1
    for index, effect in enumerate(effects):
        delay = round(float(effect["timeline_start"]) * 1000)
        filters.append(
            f"[{next_input + index}:a]aresample=48000,volume={float(effect.get('volume', 1.0)):.6f},"
            f"adelay={delay}:all=1[effect{index}]"
        )
        mix_labels.append(f"effect{index}")
    filters.append(
        "".join(f"[{label}]" for label in mix_labels)
        + f"amix=inputs={len(mix_labels)}:normalize=0:dropout_transition=0,"
        "alimiter=limit=0.95[aout]"
    )

    subtitle_path = (args.subtitle_ass or args.subtitles).resolve().as_posix().replace(":", r"\:")
    font_dir = args.font_dir.resolve().as_posix().replace(":", r"\:")
    style = (
        f"FontName=Noto Sans SemiBold,FontSize={args.subtitle_size},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=5.5,Shadow=0,Alignment=8,"
        f"MarginL=58,MarginR=58,MarginV={args.subtitle_margin_v}"
    )
    style_option = "" if args.subtitle_ass else f":force_style='{style}'"
    filters.append(
        f"[vbase]subtitles=filename='{quote_filter(subtitle_path)}':"
        f"fontsdir='{quote_filter(font_dir)}'{style_option}[vout]"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    filter_file = args.timeline.parent / "preview_filter.txt"
    filter_file.write_text(";\n".join(filters), encoding="utf-8")
    command = [str(args.ffmpeg), "-hide_banner", "-y"]
    for path in files:
        command += ["-i", path]
    command += ["-i", str(args.voice.resolve())]
    if args.music is not None:
        command += ["-i", str(args.music.resolve())]
    for effect in effects:
        command += ["-i", str(Path(effect["path"]).resolve())]
    command += [
        "-/filter_complex", str(filter_file.resolve()),
        "-map", "[vout]", "-map", "[aout]", "-t", f"{final_duration:.6f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
        "-r", str(args.fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(args.output.resolve()),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
