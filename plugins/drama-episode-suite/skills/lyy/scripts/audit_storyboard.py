#!/usr/bin/env python3
"""Audit a lyy storyboard ledger and/or visible DOCX for repeat and coverage errors."""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


FIELDS = ("人物", "场景", "情节", "画面", "微表情", "动作", "台词")
VISUAL = ("情节", "画面", "微表情", "动作")
PLACEHOLDER = re.compile(r"待补|TBD|同上|后续镜头同前|按上一镜处理", re.I)
FIELD_LINE = re.compile(r"^\s*(人物|场景|音效|情节|画面|微表情|动作|台词)\s*[：:]\s*(.*)$")
SHOT_LINE = re.compile(r"^\s*(?:镜头|分镜)\s*0*(\d+)\b")
EP_LINE = re.compile(r"^\s*EP\s*0*(\d+)\b", re.I)
GROUP_LINE = re.compile(r"^\s*第\s*0*(\d+)\s*组\b")
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def compact(value):
    return re.sub(r"\s+", "", str(value or "")).strip("。；;，, ")


def issue(level, code, where, detail):
    return {"level": level, "code": code, "where": where, "detail": detail}


def check_shots(shots, require_schema=False):
    issues = []
    seen_ids = set()
    for index, shot in enumerate(shots):
        sid = str(shot.get("id") or f"shot-{index+1}")
        if sid in seen_ids:
            issues.append(issue("ERROR", "DUPLICATE_SHOT_ID", sid, "镜号重复"))
        seen_ids.add(sid)
        fields = shot.get("fields", {})
        if require_schema:
            if list(fields) != list(FIELDS):
                issues.append(issue("ERROR", "FIELD_SCHEMA", sid, "正式七栏名称或顺序不符"))
            if not str(shot.get("goal", "")).strip():
                issues.append(issue("ERROR", "NO_VISUAL_GOAL", sid, "缺少本镜独立画面任务"))
            try:
                start, end = float(shot["start"]), float(shot["end"])
                if not 1.0 - 0.01 <= end - start <= 2.5 + 0.01:
                    issues.append(issue("ERROR", "SHOT_DURATION", sid, f"镜头时长 {end-start:.2f}s 不在 1–2.5s"))
                if index and abs(start - float(shots[index-1]["end"])) > 0.03:
                    issues.append(issue("ERROR", "TIMELINE_GAP", sid, "与上一镜时间不连续"))
            except (KeyError, ValueError, TypeError):
                issues.append(issue("ERROR", "BAD_TIME", sid, "镜头缺少有效起止时间"))
        for field in FIELDS:
            value = str(fields.get(field, ""))
            if not value.strip():
                issues.append(issue("ERROR", "EMPTY_FIELD", f"{sid}/{field}", "七栏内容为空"))
            elif field in VISUAL and PLACEHOLDER.search(value):
                issues.append(issue("ERROR", "PLACEHOLDER", f"{sid}/{field}", "出现占位描述"))
        if index == 0:
            continue
        prev = shots[index-1]
        pid = str(prev.get("id") or f"shot-{index}")
        pf = prev.get("fields", {})
        same = [f for f in VISUAL if compact(fields.get(f)) and compact(fields.get(f)) == compact(pf.get(f))]
        if len(same) >= 2:
            issues.append(issue("ERROR", "COPY_VISUAL", f"{pid} → {sid}", f"相邻镜头重复视觉栏：{'、'.join(same)}"))
        elif same:
            issues.append(issue("WARN", "REPEATED_FIELD", f"{pid} → {sid}", f"相邻镜头重复：{same[0]}，核对是否有新信息"))
        if all(len(compact(fields.get(f))) >= 18 and
               SequenceMatcher(None, compact(fields.get(f)), compact(pf.get(f))).ratio() >= 0.90
               for f in ("画面", "动作")):
            issues.append(issue("ERROR", "NEAR_COPY_VISUAL", f"{pid} → {sid}", "画面和动作高度相似"))
        current_line = compact(fields.get("台词"))
        if current_line and current_line == compact(pf.get("台词")) and len(current_line) > 18:
            issues.append(issue("ERROR", "REPEATED_SPEECH", f"{pid} → {sid}", "相邻镜头台词整段重复"))
    return issues


def check_ledger(source, ledger, require_audio_lock=False):
    issues = []
    if require_audio_lock and ledger.get("timing_status") != "audio_locked":
        issues.append(issue("ERROR", "AUDIO_NOT_LOCKED", str(ledger.get("episode", "?")), "最终稿未标记实录音频逐句锁时"))
    if source.get("episode") != ledger.get("episode"):
        issues.append(issue("ERROR", "EPISODE_MISMATCH", "episode", "原文清单与分镜集号不符"))
    units = source.get("units", [])
    if "events" not in source:
        issues.append(issue("ERROR", "NO_EVENT_MANIFEST", "source", "原文清单缺少剧情事件表 events"))
    events = source.get("events", [])
    event_ids = [event.get("id") for event in events]
    if len(event_ids) != len(set(event_ids)) or any(not event_id for event_id in event_ids):
        issues.append(issue("ERROR", "EVENT_IDS", "source", "剧情事件 ID 缺失或重复"))
    for event in events:
        if not event.get("fact") or not event.get("source_ref"):
            issues.append(issue("ERROR", "EVENT_SOURCE", str(event.get("id", "?")), "事件缺少剧情事实或原文位置"))
    ids = [u.get("id") for u in units]
    if len(ids) != len(set(ids)) or any(not uid for uid in ids):
        issues.append(issue("ERROR", "SOURCE_IDS", "source", "原文声段 ID 缺失或重复"))
    unit_by_id = {u.get("id"): u for u in units}
    for unit in units:
        if unit.get("kind") not in ("narration", "dialogue", "offscreen_dialogue") or not unit.get("speaker") or not unit.get("source_ref"):
            issues.append(issue("ERROR", "SOURCE_UNIT_META", str(unit.get("id", "?")), "声段缺少有效类型、说话人或原文位置"))
    shots = ledger.get("shots", [])
    if not shots:
        issues.append(issue("ERROR", "NO_SHOTS", "ledger", "没有镜头"))
    else:
        try:
            if abs(float(shots[0].get("start", 0))) > 0.03:
                issues.append(issue("WARN", "START_NOT_ZERO", str(ledger.get("episode", "?")), "首镜不从 0 秒开始，核对片头音频"))
        except (ValueError, TypeError):
            pass
    issues.extend(check_shots(shots, require_schema=True))
    actual = defaultdict(list)
    order = []
    used_events = []
    for shot in shots:
        sid = str(shot.get("id", "?"))
        if "event_ids" not in shot:
            issues.append(issue("ERROR", "NO_EVENT_LINK", sid, "镜头缺少 event_ids；无事件时须写空列表"))
        for event_id in shot.get("event_ids", []):
            used_events.append(event_id)
            if event_id not in event_ids:
                issues.append(issue("ERROR", "UNKNOWN_EVENT", sid, f"事件 {event_id} 不在原文清单"))
        for part in shot.get("spoken", []):
            uid = part.get("unit_id")
            text = part.get("text", "")
            order.append(uid)
            actual[uid].append(str(text))
            if uid not in ids:
                issues.append(issue("ERROR", "UNKNOWN_SPEECH", sid, f"声段 {uid} 不在原文清单"))
            visible_line = str(shot.get("fields", {}).get("台词", ""))
            if not text or text not in visible_line:
                issues.append(issue("ERROR", "SPEECH_NOT_VISIBLE", sid, f"声段 {uid} 的本镜原文片段未出现在台词栏"))
            unit = unit_by_id.get(uid, {})
            if unit.get("kind") == "narration" and "旁白" not in visible_line:
                issues.append(issue("ERROR", "VOICE_KIND", sid, f"声段 {uid} 应标明旁白"))
            elif unit.get("kind") in ("dialogue", "offscreen_dialogue") and str(unit.get("speaker")) not in visible_line:
                issues.append(issue("ERROR", "SPEAKER_MISMATCH", sid, f"声段 {uid} 未标明原文说话人 {unit.get('speaker')}"))
            try:
                ps, pe = float(part["start"]), float(part["end"])
                if ps < float(shot["start"]) - 0.03 or pe > float(shot["end"]) + 0.03 or ps >= pe:
                    issues.append(issue("ERROR", "SPEECH_TIME", sid, f"声段 {uid} 时间超出镜头或倒置"))
            except (KeyError, ValueError, TypeError):
                issues.append(issue("ERROR", "SPEECH_TIME", sid, f"声段 {uid} 缺少有效时间"))
    for unit in units:
        uid = unit.get("id")
        joined = "".join(actual.get(uid, []))
        if joined != unit.get("text", ""):
            issues.append(issue("ERROR", "SOURCE_TEXT_MISMATCH", uid, "镜头片段拼接后与原文不完全一致（漏词、改词或重复）"))
    first_order = list(dict.fromkeys(order))
    interrupted = False
    for uid in set(order):
        positions = [index for index, current in enumerate(order) if current == uid]
        if positions and any(current != uid for current in order[positions[0]:positions[-1]+1]):
            interrupted = True
            break
    if first_order != ids or interrupted:
        issues.append(issue("ERROR", "SPEECH_ORDER", "ledger", "声段顺序与原文不符或一段被隔断后重启"))
    if list(dict.fromkeys(used_events)) != event_ids:
        issues.append(issue("ERROR", "EVENT_COVERAGE", "ledger", "剧情事件缺失、错序或新增；逐项核对原剧本"))
    groups = defaultdict(list)
    group_order = []
    for shot in shots:
        group = str(shot.get("group", ""))
        groups[group].append(shot)
        group_order.append(group)
    if list(dict.fromkeys(group_order)) != [group for index, group in enumerate(group_order) if index == 0 or group != group_order[index-1]]:
        issues.append(issue("ERROR", "GROUP_REENTRY", "ledger", "组号在离开后再次出现，镜头可能跨组错排"))
    ordered_groups = list(dict.fromkeys(group_order))
    for group, group_shots in groups.items():
        if not group:
            issues.append(issue("ERROR", "NO_GROUP", "ledger", "有镜头未标组号"))
            continue
        try:
            length = float(group_shots[-1]["end"]) - float(group_shots[0]["start"])
            if length > 27.5:
                issues.append(issue("WARN", "LONG_GROUP", group, f"组长 {length:.2f}s，核对 25 秒目标与气口"))
            elif group != ordered_groups[-1] and length < 22.5:
                issues.append(issue("WARN", "SHORT_GROUP", group, f"非尾组仅 {length:.2f}s，核对组界与气口"))
        except (KeyError, ValueError, TypeError):
            pass
    return issues


def docx_shots(path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    shots = []
    episode = "EP?"
    group = "G?"
    for paragraph in root.findall(".//w:p", NS):
        line = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()
        if not line:
            continue
        episode_heading = EP_LINE.match(line)
        if episode_heading:
            episode = f"EP{episode_heading.group(1)}"
            group = "G?"
            continue
        group_heading = GROUP_LINE.match(line)
        if group_heading:
            group = f"G{group_heading.group(1)}"
            continue
        heading = SHOT_LINE.match(line)
        if heading:
            shots.append({"id": f"{episode}/{group}/镜头{heading.group(1)}", "fields": {}})
            continue
        field = FIELD_LINE.match(line)
        if field and shots:
            name, value = field.groups()
            shots[-1]["fields"][name] = value
    return shots


def check_corpus(ledgers):
    issues = []
    episode_ids = [item.get("episode") for item in ledgers]
    if len(episode_ids) != len(set(episode_ids)):
        issues.append(issue("ERROR", "DUPLICATE_EPISODE", "corpus", "多集清单出现重复集号"))
    previous = {}
    for ledger in ledgers:
        episode = str(ledger.get("episode", "?"))
        for shot in ledger.get("shots", []):
            fields = shot.get("fields", {})
            key = tuple(compact(fields.get(name)) for name in VISUAL)
            if all(key) and sum(len(part) for part in key) >= 25:
                if key in previous:
                    old_episode, old_shot = previous[key]
                    if old_episode != episode:
                        issues.append(issue("ERROR", "CROSS_EPISODE_COPY", f"{old_episode}/{old_shot} → {episode}/{shot.get('id','?')}", "跨集复用相同情节、画面、微表情和动作"))
                else:
                    previous[key] = (episode, shot.get("id", "?"))
    return issues


def check_docx_against_ledger(docx_shots, ledgers):
    issues = []
    ledger_shots = [shot for ledger in ledgers for shot in ledger.get("shots", [])]
    if len(docx_shots) != len(ledger_shots):
        return [issue("ERROR", "DOCX_SHOT_COUNT", "docx", f"Word 有 {len(docx_shots)} 镜，台账有 {len(ledger_shots)} 镜")]
    for document_shot, ledger_shot in zip(docx_shots, ledger_shots):
        for field in FIELDS:
            visible = compact(document_shot.get("fields", {}).get(field))
            expected = compact(ledger_shot.get("fields", {}).get(field))
            if visible != expected:
                issues.append(issue("ERROR", "DOCX_LEDGER_MISMATCH", f"{document_shot['id']}/{field}", "Word 正文与通过检查的台账不一致"))
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append")
    parser.add_argument("--ledger", type=Path, action="append")
    parser.add_argument("--docx", type=Path, action="append")
    parser.add_argument("--require-audio-lock", action="store_true")
    args = parser.parse_args()
    if not args.docx and not (args.source and args.ledger):
        parser.error("provide --source and --ledger, and/or --docx")
    results = []
    ledgers = []
    if args.source or args.ledger:
        if not args.source or not args.ledger or len(args.source) != len(args.ledger):
            parser.error("use one --source and one --ledger for each episode")
        for source_path, ledger_path in zip(args.source, args.ledger):
            source = json.loads(source_path.read_text(encoding="utf-8"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            results.extend(check_ledger(source, ledger, require_audio_lock=args.require_audio_lock))
            ledgers.append(ledger)
        results.extend(check_corpus(ledgers))
    if args.docx:
        visible_shots = []
        for docx_path in args.docx:
            shots = docx_shots(docx_path)
            if not shots:
                results.append(issue("ERROR", "DOCX_PARSE", str(docx_path), "未识别镜头标题；需人工核对文档结构"))
            else:
                visible_shots.extend(shots)
                results.extend(check_shots(shots))
                for shot in shots:
                    if "音效" in shot.get("fields", {}):
                        results.append(issue("ERROR", "EXTRA_SOUND_FIELD", shot["id"], "正式稿应将音效写入场景或动作栏"))
        if ledgers:
            results.extend(check_docx_against_ledger(visible_shots, ledgers))
    for item in results:
        print(f"{item['level']} {item['code']} {item['where']}: {item['detail']}")
    counts = Counter(item["level"] for item in results)
    print(f"SUMMARY errors={counts['ERROR']} warnings={counts['WARN']}")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
