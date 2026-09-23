import os
import json
import asyncio
import re
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Poll,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from telegram.request import HTTPXRequest


# =========================================================
# تنظیمات
# =========================================================

ADMIN_ID = 864502933

# زمان هر سؤال بر حسب ثانیه
QUESTION_TIME = 20

# هر پاسخ صحیح = 1 امتیاز
BASE_SCORE = 1

# هر 3 پاسخ غلط = کسر 1 امتیاز
WRONGS_FOR_ONE_PENALTY = 3

# پروکسی
PROXY = "http://127.0.0.1:8080"

# فایل ذخیره آزمون‌ها
EXAMS_FILE = "exams.json"


# =========================================================
# متغیرهای اصلی
# =========================================================

exams = {}
active = {}
polls = {}
states = {}
group_sessions = {}
group_polls = {}
share_requests = {}

share_request_seq = 1000


# =========================================================
# ذخیره و بارگذاری
# =========================================================

def save():
    with open(
        EXAMS_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            list(exams.values()),
            f,
            ensure_ascii=False,
            indent=2
        )


def load():
    global exams

    try:
        with open(
            EXAMS_FILE,
            encoding="utf-8"
        ) as f:
            data = json.load(f)

    except Exception:
        data = []

    if isinstance(data, dict):
        data = list(data.values())

    exams = {
        str(x["id"]): x
        for x in data
        if isinstance(x, dict)
        and x.get("id")
    }

    for e in exams.values():
        e.setdefault("questions", [])
        e.setdefault("results", [])
        # سازگاری با آزمون‌های قدیمی
        e.setdefault("subtitle", "بدون عنوان فرعی")


# =========================================================
# ابزارهای عمومی
# =========================================================

def is_admin(user):
    return user.id == ADMIN_ID


def kb(rows):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text,
                callback_data=data
            )
            for text, data in row
        ]
        for row in rows
    ])


def main_kb(admin=False):
    rows = [
        [
            (
                "📚 آزمون‌ها",
                "exams"
            )
        ],
        [
            (
                "🏆 رتبه‌بندی",
                "rank_menu"
            )
        ]
    ]

    if admin:
        rows.append([
            (
                "⚙️ پنل مدیریت",
                "panel"
            )
        ])

    return kb(rows)


def exam_title(e):
    name = e.get("name", "آزمون")
    subtitle = e.get("subtitle", "").strip()

    if subtitle:
        return f"📚 {name} | 📝 {subtitle}"

    return f"📚 {name}"


def exam_full_title(e):
    name = e.get("name", "آزمون")
    subtitle = e.get("subtitle", "").strip()

    if subtitle:
        return f"📚 {name}\n📝 {subtitle}"

    return f"📚 {name}"


def exam_list(prefix="exam"):
    rows = []

    for eid, e in exams.items():
        rows.append([
            (
                exam_title(e),
                f"{prefix}:{eid}"
            )
        ])

    rows.append([
        (
            "🔙 بازگشت",
            "back"
        )
    ])

    return kb(rows)


def admin_list():
    rows = []

    for eid, e in exams.items():
        rows.append([
            (
                exam_title(e),
                "ae:" + eid
            )
        ])

    rows += [
        [
            (
                "➕ ساخت آزمون",
                "create"
            )
        ],
        [
            (
                "📥 ورود فایل TXT",
                "upload"
            )
        ],
        [
            (
                "📤 ارسال آزمون به گروه",
                "send_menu"
            )
        ],
        [
            (
                "🔙 بازگشت",
                "panel"
            )
        ]
    ]

    return kb(rows)


def admin_exam_kb(eid):
    return kb([
        [
            (
                "✏️ تغییر عنوان",
                "rn:" + eid
            )
        ],
        [
            (
                "📥 ورود فایل TXT",
                "upload:" + eid
            )
        ],
        [
            (
                "📤 اشتراک آزمون به گروه",
                "share:" + eid
            )
        ],
        [
            (
                "▶️ شروع آزمون",
                "start:" + eid
            )
        ],
        [
            (
                "🏆 رتبه‌بندی",
                "rank:" + eid
            )
        ],
        [
            (
                "🗑 حذف آزمون",
                "del:" + eid
            )
        ],
        [
            (
                "🔙 بازگشت",
                "admin_exams"
            )
        ]
    ])


# =========================================================
# سیستم امتیاز
# =========================================================

def calculate_final_score(correct, wrong):
    """
    هر پاسخ صحیح = 1 امتیاز
    هر 3 پاسخ غلط = کسر 1 امتیاز
    حداقل امتیاز نهایی = 0
    """

    correct = max(
        0,
        int(correct or 0)
    )

    wrong = max(
        0,
        int(wrong or 0)
    )

    penalty = (
        wrong // WRONGS_FOR_ONE_PENALTY
    )

    return max(
        0,
        correct - penalty
    )


# =========================================================
# انتخاب گروه
# =========================================================

def make_share_request(
    user_id,
    eid
):
    global share_request_seq

    share_request_seq += 1

    if share_request_seq > 2147483000:
        share_request_seq = 1001

    rid = share_request_seq

    share_requests[rid] = {
        "uid": user_id,
        "eid": eid
    }

    return rid


def share_group_kb(request_id):
    return ReplyKeyboardMarkup(
        [[
            KeyboardButton(
                "📤 انتخاب گروه",
                request_chat=KeyboardButtonRequestChat(
                    request_id=request_id,
                    chat_is_channel=False,
                    bot_is_member=True,
                )
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# =========================================================
# آزمون گروهی
# =========================================================

def ensure_group_session(
    chat,
    eid
):
    if chat in group_sessions:
        session = group_sessions[chat]

        if (
            session.get("eid") != eid
            and session.get("phase")
            in (
                "register",
                "running"
            )
        ):
            return None

        return session

    e = exams.get(eid)

    if not e:
        return None

    if not e.get("questions"):
        return None

    session = {
        "eid": eid,

        "name": e.get(
            "name",
            "آزمون"
        ),

        "subtitle": e.get(
            "subtitle",
            ""
        ),

        "questions": e.get(
            "questions",
            []
        ),

        "phase": "register",

        "participants": {},

        "i": 0,

        "poll_id": None,

        "task": None,

        "announcement_message_id": None,

        "control_message_id": None,
    }

    group_sessions[chat] = session

    return session


def group_register_kb(
    eid,
    show_start=False
):
    rows = [
        [
            (
                "✅ اعلام آمادگی / شرکت در آزمون",
                f"gjoin:{eid}"
            )
        ]
    ]

    if show_start:
        rows.append([
            (
                "▶️ شروع آزمون",
                f"gstart:{eid}"
            )
        ])

    return kb(rows)


def group_control_kb():
    return kb([
        [
            (
                "🛑 توقف آزمون من",
                "gstop"
            )
        ]
    ])


def private_control_kb():
    return kb([
        [
            (
                "🛑 توقف آزمون",
                "stop"
            )
        ]
    ])


def participant_name(user):
    return (
        user.full_name
        or user.username
        or str(user.id)
    )


def make_participant(user):
    return {
        "uid": user.id,

        "name": participant_name(user),

        "correct": 0,

        "wrong": 0,

        "blank": 0,

        "answered": 0,

        "finished": False,

        "active": True
    }


# =========================================================
# پاک کردن پیام کنترل خصوصی
# =========================================================

async def delete_private_control(
    chat,
    ctx,
    d
):
    message_id = d.get(
        "control_message_id"
    )

    if not message_id:
        return

    try:
        await ctx.bot.delete_message(
            chat_id=chat,
            message_id=message_id
        )

    except Exception:
        pass

    d["control_message_id"] = None


# =========================================================
# پاک کردن پیام کنترل گروه
# =========================================================

async def delete_group_control(
    chat,
    ctx,
    s
):
    message_id = s.get(
        "control_message_id"
    )

    if not message_id:
        return

    try:
        await ctx.bot.delete_message(
            chat_id=chat,
            message_id=message_id
        )

    except Exception:
        pass

    s["control_message_id"] = None


# =========================================================
# ارسال دکمه توقف خصوصی
# =========================================================

async def send_private_control(
    chat,
    ctx,
    d
):
    await delete_private_control(
        chat,
        ctx,
        d
    )

    try:
        msg = await ctx.bot.send_message(
            chat_id=chat,
            text="👇 کنترل آزمون:",
            reply_markup=private_control_kb()
        )

        d["control_message_id"] = msg.message_id

    except Exception:
        d["control_message_id"] = None


# =========================================================
# ارسال دکمه توقف گروه
# =========================================================

async def send_group_control(
    chat,
    ctx,
    s
):
    await delete_group_control(
        chat,
        ctx,
        s
    )

    try:
        msg = await ctx.bot.send_message(
            chat_id=chat,
            text="👇 هر شرکت‌کننده می‌تواند آزمون خودش را متوقف کند:",
            reply_markup=group_control_kb()
        )

        s["control_message_id"] = msg.message_id

    except Exception:
        s["control_message_id"] = None


# =========================================================
# متن نتایج گروهی
# =========================================================

def group_result_text(
    session,
    title="🏆 نتایج آزمون"
):
    rows = []

    total = len(
        session["questions"]
    )

    for p in session["participants"].values():

        correct = p.get(
            "correct",
            0
        )

        wrong = p.get(
            "wrong",
            0
        )

        blank = max(
            0,
            total
            - correct
            - wrong
        )

        score = calculate_final_score(
            correct,
            wrong
        )

        penalty = (
            wrong // WRONGS_FOR_ONE_PENALTY
        )

        rows.append((
            score,
            correct,
            -wrong,
            -blank,
            p["name"],
            correct,
            wrong,
            blank,
            penalty
        ))

    rows.sort(
        reverse=True
    )

    out = [
        title,
        "",
        f"📚 {session['name']}",
        (
            f"📝 {session.get('subtitle', '')}\n"
            if session.get("subtitle")
            else ""
        ),
        f"👥 شرکت‌کنندگان: {len(rows)}",
        ""
    ]

    for n, r in enumerate(
        rows,
        1
    ):
        out.append(
            f"{n}. {r[4]}\n"
            f"   🎯 امتیاز نهایی: {r[0]}\n"
            f"   ✅ درست: {r[5]} | "
            f"❌ غلط: {r[6]} | "
            f"⏳ نزده: {r[7]}"
        )

    return "\n".join(out)


# =========================================================
# پایان آزمون گروهی
# =========================================================

async def finish_group(
    chat_id,
    ctx,
    reason="done"
):
    session = group_sessions.pop(
        chat_id,
        None
    )

    if not session:
        return

    task = session.get(
        "task"
    )

    if task and not task.done():
        task.cancel()

    poll_id = session.get(
        "poll_id"
    )

    if poll_id:
        info = group_polls.pop(
            poll_id,
            None
        )

        if info:
            try:
                await ctx.bot.stop_poll(
                    chat_id=chat_id,
                    message_id=info["message_id"]
                )
            except Exception:
                pass

    await delete_group_control(
        chat_id,
        ctx,
        session
    )

    e = exams.get(
        session["eid"]
    )

    if e:
        e.setdefault(
            "results",
            []
        )

        total = len(
            session["questions"]
        )

        for p in session["participants"].values():

            correct = p.get(
                "correct",
                0
            )

            wrong = p.get(
                "wrong",
                0
            )

            blank = max(
                0,
                total
                - correct
                - wrong
            )

            final = calculate_final_score(
                correct,
                wrong
            )

            penalty = (
                wrong // WRONGS_FOR_ONE_PENALTY
            )

            percent = (
                final
                / total
                * 100
                if total
                else 0
            )

            e["results"] = [
                r
                for r in e["results"]
                if r.get("uid") != p["uid"]
            ]

            e["results"].append({
                "uid": p["uid"],

                "name": p["name"],

                "score": final,

                "correct": correct,

                "wrong": wrong,

                "blank": blank,

                "penalty": penalty,

                "total": total,

                "percent": round(
                    percent,
                    2
                ),

                "date": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            })

        save()

    if reason == "done":
        title = "🎉 آزمون تمام شد"

    elif reason == "stop":
        title = "🛑 آزمون متوقف شد"

    else:
        title = "⏰ زمان آزمون تمام شد"

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=group_result_text(
            session,
            title
        )
    )


# =========================================================
# ارسال سؤال گروهی
# =========================================================

async def send_group_question(
    chat_id,
    ctx
):
    s = group_sessions.get(
        chat_id
    )

    if not s:
        return

    if not any(
        p["active"]
        and not p["finished"]
        for p in s["participants"].values()
    ):
        await finish_group(
            chat_id,
            ctx,
            "stop"
        )
        return

    if s["i"] >= len(
        s["questions"]
    ):
        await finish_group(
            chat_id,
            ctx,
            "done"
        )
        return

    q = s["questions"][s["i"]]

    correct = q.get(
        "answer"
    )

    if correct is None:
        await finish_group(
            chat_id,
            ctx,
            "stop"
        )
        return

    try:
        correct = int(correct)

    except Exception:
        await finish_group(
            chat_id,
            ctx,
            "stop"
        )
        return

    if correct < 0 or correct > 3:
        await finish_group(
            chat_id,
            ctx,
            "stop"
        )
        return

    old_poll_id = s.get(
        "poll_id"
    )

    if old_poll_id:
        old_info = group_polls.pop(
            old_poll_id,
            None
        )

        if old_info:
            try:
                await ctx.bot.stop_poll(
                    chat_id=chat_id,
                    message_id=old_info["message_id"]
                )
            except Exception:
                pass

        s["poll_id"] = None

    await delete_group_control(
        chat_id,
        ctx,
        s
    )

    question_text = (
        f"📚 درس: {s['name']}\n"
        + (
            f"📝 بخش: {s.get('subtitle')}\n"
            if s.get("subtitle")
            else ""
        )
        + f"🔢 سؤال {s['i'] + 1} "
        f"از {len(s['questions'])}\n"
        f"⏱️ زمان: {QUESTION_TIME} ثانیه\n\n"
        f"{q['question']}"
    )

    try:
        message = await ctx.bot.send_poll(
            chat_id=chat_id,

            question=question_text,

            options=q["options"],

            type=Poll.QUIZ,

            correct_option_id=correct,

            allows_multiple_answers=False,

            is_anonymous=False,

            open_period=QUESTION_TIME,

            explanation=(
                "✅ پاسخ صحیح: "
                + q["options"][correct]
            )[:200]
        )

    except Exception as ex:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ ارسال سؤال انجام نشد.\n\n"
                f"{ex}"
            )
        )

        await finish_group(
            chat_id,
            ctx,
            "stop"
        )

        return

    s["poll_id"] = message.poll.id

    group_polls[
        message.poll.id
    ] = {
        "chat_id": chat_id,

        "index": s["i"],

        "correct": correct,

        "message_id": message.message_id,

        "answered": set()
    }

    await send_group_control(
        chat_id,
        ctx,
        s
    )

    old = s.get(
        "task"
    )

    if old and not old.done():
        old.cancel()

    s["task"] = asyncio.create_task(
        group_timeout(
            chat_id,
            s["i"],
            ctx
        )
    )


# =========================================================
# زمان سؤال گروهی
# =========================================================

async def group_timeout(
    chat_id,
    index,
    ctx
):
    try:
        await asyncio.sleep(
            QUESTION_TIME + 1
        )

    except asyncio.CancelledError:
        return

    s = group_sessions.get(
        chat_id
    )

    if not s:
        return

    if s.get("i") != index:
        return

    poll_id = s.get(
        "poll_id"
    )

    info = group_polls.pop(
        poll_id,
        None
    )

    if info:
        try:
            await ctx.bot.stop_poll(
                chat_id=chat_id,
                message_id=info["message_id"]
            )
        except Exception:
            pass

        for uid, p in s["participants"].items():

            if (
                p["active"]
                and not p["finished"]
                and uid not in info["answered"]
            ):
                p["blank"] += 1

    s["i"] += 1

    s["poll_id"] = None

    await send_group_question(
        chat_id,
        ctx
    )


# =========================================================
# توقف آزمون یک شخص در گروه
# =========================================================

async def finish_person(
    chat_id,
    uid,
    ctx
):
    s = group_sessions.get(
        chat_id
    )

    if not s:
        return

    p = s["participants"].get(
        uid
    )

    if not p:
        return

    if p["finished"]:
        return

    p["finished"] = True
    p["active"] = False

    total = len(
        s["questions"]
    )

    correct = p.get(
        "correct",
        0
    )

    wrong = p.get(
        "wrong",
        0
    )

    blank = max(
        0,
        total
        - correct
        - wrong
    )

    final = calculate_final_score(
        correct,
        wrong
    )

    penalty = (
        wrong // WRONGS_FOR_ONE_PENALTY
    )

    await ctx.bot.send_message(
        chat_id=chat_id,

        text=(
            f"🛑 {p['name']} "
            "آزمون را متوقف کرد.\n\n"

            f"📘 آزمون: {s['name']}\n"

            f"📊 سؤال فعلی: "
            f"{min(s['i'] + 1, total)} از {total}\n\n"

            f"🎯 امتیاز نهایی: {final}\n"

            f"✅ درست: {correct}\n"

            f"❌ غلط: {wrong}\n"

            f"⏳ نزده: {blank}\n"

            f"📉 کسر امتیاز: {penalty}"
        )
    )

    if not any(
        x["active"]
        and not x["finished"]
        for x in s["participants"].values()
    ):
        await finish_group(
            chat_id,
            ctx,
            "stop"
        )


# =========================================================
# پایان آزمون خصوصی
# =========================================================

async def finish(
    chat,
    ctx,
    why="done"
):
    d = active.pop(
        chat,
        None
    )

    if not d:
        return

    task = d.get(
        "task"
    )

    if task and not task.done():
        task.cancel()

    poll_id = d.get(
        "poll"
    )

    if poll_id:
        info = polls.pop(
            poll_id,
            None
        )

        if info:
            try:
                await ctx.bot.stop_poll(
                    chat_id=chat,
                    message_id=info["message_id"]
                )
            except Exception:
                pass

    await delete_private_control(
        chat,
        ctx,
        d
    )

    e = exams.get(
        d["eid"]
    )

    if not e:
        return

    total = len(
        d["qs"]
    )

    correct = d.get(
        "correct",
        0
    )

    wrong = d.get(
        "wrong",
        0
    )

    final = calculate_final_score(
        correct,
        wrong
    )

    penalty = (
        wrong // WRONGS_FOR_ONE_PENALTY
    )

    answered = d.get(
        "answered",
        0
    )

    blank = max(
        0,
        total - answered
    )

    percent = (
        final
        / total
        * 100
        if total
        else 0
    )

    e.setdefault(
        "results",
        []
    )

    e["results"] = [
        r
        for r in e["results"]
        if r.get("uid") != d["uid"]
    ]

    e["results"].append({
        "uid": d["uid"],

        "name": d["name"],

        "score": final,

        "correct": correct,

        "wrong": wrong,

        "blank": blank,

        "penalty": penalty,

        "total": total,

        "percent": round(
            percent,
            2
        ),

        "date": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    })

    save()

    if why == "done":
        title = "🎉 آزمون تمام شد"

    elif why == "stop":
        title = "🛑 آزمون متوقف شد"

    else:
        title = "⏰ زمان آزمون تمام شد"

    text_result = (
        f"{title}\n\n"

        f"📚 درس: {e['name']}\n"
        + (
            f"📝 بخش: {e.get('subtitle')}\n\n"
            if e.get("subtitle")
            else "\n"
        )
        + f"📝 تعداد سؤال: {total}\n"

        f"⏱️ زمان هر سؤال: "
        f"{QUESTION_TIME} ثانیه\n\n"

        f"🎯 امتیاز نهایی: {final}\n"

        f"✅ صحیح: {correct}\n"

        f"❌ غلط: {wrong}\n"

        f"⏳ نزده: {blank}\n"

        f"📉 کسر امتیاز: {penalty}\n"

        f"📊 درصد: {percent:.2f}%"
    )

    await ctx.bot.send_message(
        chat_id=chat,

        text=text_result,

        reply_markup=main_kb(
            d["admin"]
        )
    )


# =========================================================
# ارسال سؤال خصوصی
# =========================================================

async def sendq(
    chat,
    ctx
):
    d = active.get(
        chat
    )

    if not d:
        return

    if d["i"] >= len(
        d["qs"]
    ):
        await finish(
            chat,
            ctx,
            "done"
        )
        return

    q = d["qs"][d["i"]]

    correct = q.get(
        "answer"
    )

    if correct is None:
        await finish(
            chat,
            ctx,
            "stop"
        )
        return

    try:
        correct = int(correct)

    except Exception:
        await finish(
            chat,
            ctx,
            "stop"
        )
        return

    if correct < 0 or correct > 3:
        await finish(
            chat,
            ctx,
            "stop"
        )
        return

    old_poll_id = d.get(
        "poll"
    )

    if old_poll_id:
        old_info = polls.pop(
            old_poll_id,
            None
        )

        if old_info:
            try:
                await ctx.bot.stop_poll(
                    chat_id=chat,
                    message_id=old_info["message_id"]
                )
            except Exception:
                pass

        d["poll"] = None

    await delete_private_control(
        chat,
        ctx,
        d
    )

    question_text = (
        f"📚 درس: "
        f"{exams.get(d['eid'], {}).get('name', 'آزمون')}\n"
        + (
            f"📝 بخش: {exams.get(d['eid'], {}).get('subtitle', '')}\n"
            if exams.get(d['eid'], {}).get('subtitle')
            else ""
        )
        + f"🔢 سؤال {d['i'] + 1} "
        f"از {len(d['qs'])}\n"

        f"⏱️ زمان: {QUESTION_TIME} ثانیه\n\n"

        f"{q['question']}"
    )

    try:
        message = await ctx.bot.send_poll(
            chat_id=chat,

            question=question_text,

            options=q["options"],

            type=Poll.QUIZ,

            correct_option_id=correct,

            allows_multiple_answers=False,

            is_anonymous=False,

            open_period=QUESTION_TIME,

            explanation=(
                "✅ پاسخ صحیح: "
                + q["options"][correct]
            )[:200]
        )

    except Exception as ex:
        await ctx.bot.send_message(
            chat_id=chat,

            text=(
                "❌ ارسال سؤال انجام نشد.\n\n"
                f"{ex}"
            )
        )

        await finish(
            chat,
            ctx,
            "stop"
        )

        return

    polls[
        message.poll.id
    ] = {
        "chat_id": chat,

        "index": d["i"],

        "correct": correct,

        "message_id": message.message_id
    }

    d["poll"] = message.poll.id

    await send_private_control(
        chat,
        ctx,
        d
    )

    old_task = d.get(
        "task"
    )

    if old_task and not old_task.done():
        old_task.cancel()

    d["task"] = asyncio.create_task(
        timeout(
            chat,
            d["i"],
            ctx
        )
    )


# =========================================================
# تایم‌اوت آزمون خصوصی
# =========================================================

async def timeout(
    chat,
    index,
    ctx
):
    try:
        await asyncio.sleep(
            QUESTION_TIME + 1
        )

    except asyncio.CancelledError:
        return

    d = active.get(
        chat
    )

    if not d:
        return

    if d["i"] != index:
        return

    poll_id = d.get(
        "poll"
    )

    if poll_id:
        info = polls.pop(
            poll_id,
            None
        )

        if info:
            d["blank"] = d.get(
                "blank",
                0
            ) + 1

            try:
                await ctx.bot.stop_poll(
                    chat_id=chat,
                    message_id=info["message_id"]
                )
            except Exception:
                pass

    d["i"] += 1

    d["poll"] = None

    await asyncio.sleep(
        0.5
    )

    await sendq(
        chat,
        ctx
    )


# =========================================================
# /start
# =========================================================

async def start(
    update,
    ctx
):
    u = update.effective_user

    await update.message.reply_text(
        "🤖 ربات آزمون\n\n"
        "یک گزینه را انتخاب کنید:",

        reply_markup=main_kb(
            is_admin(u)
        )
    )


# =========================================================
# /panel
# =========================================================

async def panel(
    update,
    ctx
):
    if is_admin(
        update.effective_user
    ):
        await update.message.reply_text(
            "⚙️ پنل مدیریت",

            reply_markup=kb([
                [
                    (
                        "📚 مدیریت آزمون‌ها",
                        "admin_exams"
                    )
                ],
                [
                    (
                        "➕ ساخت آزمون",
                        "create"
                    )
                ],
                [
                    (
                        "📥 ورود فایل TXT",
                        "upload"
                    )
                ],
                [
                    (
                        "📤 ارسال آزمون به گروه",
                        "send_menu"
                    )
                ],
                [
                    (
                        "📊 آمار",
                        "stats"
                    )
                ],
                [
                    (
                        "🔙 بازگشت",
                        "back"
                    )
                ]
            ])
        )

    else:
        await update.message.reply_text(
            "⛔ دسترسی ندارید."
        )


# =========================================================
# پاسخ به Poll
# =========================================================

async def polls_answer(
    update,
    ctx
):
    p = update.poll_answer

    if not p.option_ids:
        return

    # =====================================================
    # Poll گروهی
    # =====================================================

    info = group_polls.get(
        p.poll_id
    )

    if info:
        chat = info["chat_id"]

        s = group_sessions.get(
            chat
        )

        if not s:
            return

        if s.get("i") != info["index"]:
            return

        uid = p.user.id

        person = s["participants"].get(
            uid
        )

        if not person:
            return

        if person["finished"]:
            return

        if uid in info["answered"]:
            return

        info["answered"].add(
            uid
        )

        person["answered"] += 1

        selected = p.option_ids[0]

        # پاسخ صحیح
        if selected == info["correct"]:
            person["correct"] += 1

        # پاسخ غلط
        else:
            person["wrong"] += 1

        active_users = [
            uid2
            for uid2, person2
            in s["participants"].items()
            if (
                person2["active"]
                and not person2["finished"]
            )
        ]

        if active_users and all(
            uid2 in info["answered"]
            for uid2 in active_users
        ):
            task = s.get(
                "task"
            )

            if task and not task.done():
                task.cancel()

            try:
                await ctx.bot.stop_poll(
                    chat_id=chat,
                    message_id=info["message_id"]
                )
            except Exception:
                pass

            group_polls.pop(
                p.poll_id,
                None
            )

            s["poll_id"] = None

            s["i"] += 1

            await asyncio.sleep(
                0.7
            )

            await send_group_question(
                chat,
                ctx
            )

        return

    # =====================================================
    # Poll خصوصی
    # =====================================================

    info = polls.get(
        p.poll_id
    )

    if not info:
        return

    chat = info["chat_id"]

    index = info["index"]

    correct = info["correct"]

    message_id = info["message_id"]

    d = active.get(
        chat
    )

    if not d:
        polls.pop(
            p.poll_id,
            None
        )
        return

    if d["i"] != index:
        return

    polls.pop(
        p.poll_id,
        None
    )

    task = d.get(
        "task"
    )

    if task and not task.done():
        task.cancel()

    d["answered"] = (
        d.get(
            "answered",
            0
        )
        + 1
    )

    selected = p.option_ids[0]

    # پاسخ صحیح
    if selected == correct:
        d["correct"] = (
            d.get(
                "correct",
                0
            )
            + 1
        )

    # پاسخ غلط
    else:
        d["wrong"] = (
            d.get(
                "wrong",
                0
            )
            + 1
        )

    # =====================================================
    # بستن فوری Quiz
    # =====================================================

    try:
        await ctx.bot.stop_poll(
            chat_id=chat,
            message_id=message_id
        )

    except Exception:
        pass

    d["i"] += 1

    d["poll"] = None

    await asyncio.sleep(
        0.8
    )

    await sendq(
        chat,
        ctx
    )


# =========================================================
# دکمه‌ها
# =========================================================

async def buttons(
    update,
    ctx
):
    q = update.callback_query

    await q.answer()

    u = q.from_user

    c = q.data

    chat = q.message.chat_id

    # =====================================================
    # اعلام آمادگی گروه
    # =====================================================

    if c.startswith("gjoin:"):

        eid = c.split(
            ":",
            1
        )[1]

        session = ensure_group_session(
            chat,
            eid
        )

        if not session:

            await q.answer(
                "آزمون پیدا نشد یا آزمون دیگری در حال اجراست.",
                show_alert=True
            )

            return

        if session["phase"] != "register":

            await q.answer(
                "آزمون شروع شده است.",
                show_alert=True
            )

            return

        uid = u.id

        if uid not in session["participants"]:

            session["participants"][uid] = (
                make_participant(u)
            )

        try:

            await q.message.edit_reply_markup(
                reply_markup=group_register_kb(
                    eid,
                    show_start=True
                )
            )

        except Exception:
            pass

        await q.message.reply_text(
            f"✅ {participant_name(u)} "
            "برای آزمون اعلام آمادگی کرد."
        )

        return

    # =====================================================
    # شروع آزمون گروه
    # =====================================================

    if c.startswith("gstart:"):

        if u.id != ADMIN_ID:

            await q.answer(
                "فقط مدیر می‌تواند آزمون را شروع کند.",
                show_alert=True
            )

            return

        eid = c.split(
            ":",
            1
        )[1]

        session = ensure_group_session(
            chat,
            eid
        )

        if not session:

            await q.answer(
                "آزمون پیدا نشد.",
                show_alert=True
            )

            return

        if session["phase"] != "register":

            await q.answer(
                "آزمون قبلاً شروع شده یا تمام شده است.",
                show_alert=True
            )

            return

        if not session["participants"]:

            await q.answer(
                "هنوز کسی اعلام آمادگی نکرده است.",
                show_alert=True
            )

            return

        session["phase"] = "running"

        try:

            await q.message.edit_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        await q.answer(
            "آزمون شروع شد."
        )

        await send_group_question(
            chat,
            ctx
        )

        return

    # =====================================================
    # توقف شخصی در گروه
    # =====================================================

    if c == "gstop":

        s = group_sessions.get(
            chat
        )

        if not s:

            await q.answer(
                "❌ آزمون فعالی نیست.",
                show_alert=True
            )

            return

        if u.id not in s["participants"]:

            await q.answer(
                "⛔ شما در آزمون شرکت نکرده‌اید.",
                show_alert=True
            )

            return

        await q.answer(
            "🛑 آزمون شما متوقف شد.",
            show_alert=True
        )

        await finish_person(
            chat,
            u.id,
            ctx
        )

        return

    # =====================================================
    # توقف آزمون خصوصی
    # =====================================================

    if c == "stop":

        if chat not in active:

            await q.answer(
                "❌ آزمون فعالی وجود ندارد.",
                show_alert=True
            )

            return

        await q.answer(
            "🛑 آزمون متوقف شد."
        )

        await finish(
            chat,
            ctx,
            "stop"
        )

        return

    # =====================================================
    # بازگشت
    # =====================================================

    if c == "back":

        await q.edit_message_text(
            "🏠 منوی اصلی:",

            reply_markup=main_kb(
                is_admin(u)
            )
        )

        return

    # =====================================================
    # پنل
    # =====================================================

    if c == "panel":

        if is_admin(u):

            await q.edit_message_text(
                "⚙️ پنل مدیریت",

                reply_markup=kb([
                    [
                        (
                            "📚 مدیریت آزمون‌ها",
                            "admin_exams"
                        )
                    ],
                    [
                        (
                            "➕ ساخت آزمون",
                            "create"
                        )
                    ],
                    [
                        (
                            "📥 ورود فایل TXT",
                            "upload"
                        )
                    ],
                    [
                        (
                            "📤 ارسال آزمون به گروه",
                            "send_menu"
                        )
                    ],
                    [
                        (
                            "📊 آمار",
                            "stats"
                        )
                    ],
                    [
                        (
                            "🔙 بازگشت",
                            "back"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # آزمون‌ها
    # =====================================================

    if c == "exams":

        await q.edit_message_text(
            "📚 آزمون موردنظر را انتخاب کنید:",

            reply_markup=exam_list()
        )

        return

    # =====================================================
    # رتبه‌بندی کلی
    # =====================================================

    if c == "rank_menu":

        await q.edit_message_text(
            "🏆 آزمون را انتخاب کنید:",

            reply_markup=exam_list("rank")
        )

        return

    # =====================================================
    # مدیریت آزمون‌ها
    # =====================================================

    if c == "admin_exams":

        if is_admin(u):

            await q.edit_message_text(
                "📚 مدیریت آزمون‌ها:",

                reply_markup=admin_list()
            )

        return

    # =====================================================
    # ورود فایل
    # =====================================================

    if c == "upload":

        if is_admin(u):

            states[u.id] = {
                "a": "upload_wait"
            }

            await q.edit_message_text(

                "📥 فایل TXT تست‌ها را ارسال کنید.\n\n"

                "فرمت قابل قبول:\n\n"

                "1. پایتخت ایران کدام است؟\n"
                "A) شیراز\n"
                "B) تهران\n"
                "C) اصفهان\n"
                "D) تبریز\n"
                "ANSWER: B) تهران\n\n"

                "2. پایتخت فرانسه کدام است؟\n"
                "A) برلین\n"
                "B) رم\n"
                "C) پاریس\n"
                "D) مادرید\n"
                "ANSWER: C) پاریس",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "panel"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # ورود فایل مستقیم به آزمون
    # =====================================================

    if c.startswith("upload:"):

        if is_admin(u):

            eid = c[7:]

            states[u.id] = {
                "a": "upload_wait",
                "eid": eid
            }

            await q.edit_message_text(

                "📥 حالا فایل TXT را ارسال کنید.",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "admin_exams"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # ساخت آزمون
    # =====================================================

    if c == "create":

        if is_admin(u):

            states[u.id] = {
                "a": "create_name"
            }

            await q.edit_message_text(

                "➕ عنوان اصلی را ارسال کنید.\n\n"
                "مثال: ICDL",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "panel"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # ارسال آزمون به گروه
    # =====================================================

    if c == "send_menu":

        if is_admin(u):

            await q.edit_message_text(
                "📤 آزمون را انتخاب کنید:",

                reply_markup=exam_list("sg")
            )

        return

    # =====================================================
    # آمار
    # =====================================================

    if c == "stats":

        if is_admin(u):

            await q.edit_message_text(

                f"📊 آمار\n\n"

                f"آزمون‌ها: {len(exams)}\n"

                f"سؤالات: "
                f"{sum(len(e['questions']) for e in exams.values())}\n"

                f"نتایج: "
                f"{sum(len(e['results']) for e in exams.values())}",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "panel"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # مدیریت آزمون
    # =====================================================

    if c.startswith("ae:"):

        if is_admin(u):

            await q.edit_message_text(
                "⚙️ مدیریت آزمون",

                reply_markup=admin_exam_kb(
                    c[3:]
                )
            )

        return

    # =====================================================
    # نمایش آزمون
    # =====================================================

    if c.startswith("exam:"):

        eid = c[5:]

        e = exams.get(
            eid
        )

        if e:

            await q.edit_message_text(

                exam_full_title(e) + "\n\n"

                f"📝 {len(e['questions'])} سؤال",

                reply_markup=kb([
                    [
                        (
                            "▶️ شروع",
                            "start:" + eid
                        )
                    ],
                    [
                        (
                            "🏆 رتبه‌بندی",
                            "rank:" + eid
                        )
                    ],
                    [
                        (
                            "🔙 بازگشت",
                            "exams"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # شروع آزمون خصوصی
    # =====================================================

    if c.startswith("start:"):

        eid = c[6:]

        e = exams.get(
            eid
        )

        if not e or not e.get(
            "questions"
        ):

            await q.edit_message_text(

                "❌ این آزمون سؤال ندارد.",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "exams"
                        )
                    ]
                ])
            )

            return

        missing = [
            i + 1
            for i, question
            in enumerate(
                e["questions"]
            )
            if question.get("answer") is None
        ]

        if missing:

            await q.edit_message_text(

                "❌ این آزمون قابل اجرا نیست.\n\n"

                "سؤال‌های بدون پاسخ صحیح:\n"

                + ", ".join(
                    map(str, missing)
                )
            )

            return

        if chat in active:

            await q.edit_message_text(

                "⚠️ شما یک آزمون فعال دارید.",

                reply_markup=kb([
                    [
                        (
                            "🛑 توقف آزمون",
                            "stop"
                        )
                    ],
                    [
                        (
                            "🔙 بازگشت",
                            "exams"
                        )
                    ]
                ])
            )

            return

        active[chat] = {

            "eid": eid,

            "qs": [
                dict(q)
                for q in e["questions"]
            ],

            "i": 0,

            "correct": 0,

            "wrong": 0,

            "blank": 0,

            "answered": 0,

            "poll": None,

            "task": None,

            "control_message_id": None,

            "uid": u.id,

            "name": participant_name(u),

            "admin": is_admin(u)
        }

        await q.edit_message_text(

            f"🚀 {exam_full_title(e)}\n"
            f"شروع شد.\n\n"

            f"📝 تعداد سؤال: {len(e['questions'])}\n"

            f"⏱️ زمان هر سؤال: {QUESTION_TIME} ثانیه\n\n"

            "🎯 هر پاسخ صحیح = ۱ امتیاز\n"

            "❌ هر ۳ پاسخ غلط = کسر ۱ امتیاز\n\n"

            "⏳ پاسخ ندادن تا پایان زمان = ۰ امتیاز"
        )

        await sendq(
            chat,
            ctx
        )

        return

    # =====================================================
    # رتبه‌بندی
    # =====================================================

    if c.startswith("rank:"):

        await showrank(
            q,
            c[5:],
            u
        )

        return

    # =====================================================
    # تغییر نام
    # =====================================================

    if c.startswith("rn:"):

        if is_admin(u):

            states[u.id] = {
                "a": "rename_name",
                "eid": c[3:]
            }

            await q.edit_message_text(

                "📚 عنوان اصلی جدید را ارسال کنید.\n\n"
                "مثال: ICDL",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "admin_exams"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # حذف
    # =====================================================

    if c.startswith("del:"):

        if is_admin(u):

            states[u.id] = {
                "a": "delete",
                "eid": c[4:]
            }

            await q.edit_message_text(

                "برای حذف، کلمه «حذف» را ارسال کنید.",

                reply_markup=kb([
                    [
                        (
                            "🔙 بازگشت",
                            "admin_exams"
                        )
                    ]
                ])
            )

        return

    # =====================================================
    # اشتراک آزمون با گروه
    # =====================================================

    if (
        c.startswith("share:")
        or c.startswith("sg:")
    ):

        if not is_admin(u):

            await q.answer(
                "دسترسی ندارید.",
                show_alert=True
            )

            return

        eid = c.split(
            ":",
            1
        )[1]

        e = exams.get(
            eid
        )

        if not e or not e.get(
            "questions"
        ):

            await q.answer(
                "این آزمون هنوز سؤال ندارد.",
                show_alert=True
            )

            return

        missing = [
            i + 1
            for i, question
            in enumerate(
                e["questions"]
            )
            if question.get("answer") is None
        ]

        if missing:

            await q.answer(
                "این آزمون سؤال بدون پاسخ صحیح دارد.",
                show_alert=True
            )

            return

        rid = make_share_request(
            u.id,
            eid
        )

        await ctx.bot.send_message(
            chat_id=u.id,

            text=(
                f"📤 اشتراک آزمون "
                f"«{e['name']}»\n\n"

                "فقط دکمه "
                "«📤 انتخاب گروه» "
                "را بزن و گروه را انتخاب کن.\n\n"

                "⚠️ ربات باید از قبل داخل گروه باشد."
            ),

            reply_markup=share_group_kb(
                rid
            )
        )

        await q.edit_message_text(

            "✅ آماده شد.\n\n"

            "حالا فقط دکمه "
            "«📤 انتخاب گروه» "
            "را بزن.",

            reply_markup=admin_exam_kb(
                eid
            )
        )

        return


# =========================================================
# رتبه‌بندی
# =========================================================

async def showrank(
    q,
    eid,
    u
):
    e = exams.get(
        eid
    )

    if not e:
        return

    results = e.get(
        "results",
        []
    )

    if not results:

        await q.edit_message_text(

            f"🏆 رتبه‌بندی "
            f"«{exam_full_title(e)}»\n\n"

            "هنوز نتیجه‌ای ثبت نشده است.",

            reply_markup=kb([
                [
                    (
                        "🔙 بازگشت",
                        "exams"
                    )
                ]
            ])
        )

        return

    results = sorted(
        results,

        key=lambda r: (
            r.get(
                "score",
                0
            ),

            r.get(
                "correct",
                0
            ),

            -r.get(
                "wrong",
                0
            )
        ),

        reverse=True
    )

    lines = [
        f"🏆 رتبه‌بندی "
        f"«{exam_full_title(e)}»",
        ""
    ]

    for n, r in enumerate(
        results,
        1
    ):

        lines.append(

            f"{n}. {r.get('name', 'بدون نام')} — "

            f"🎯 {r.get('score', 0)} امتیاز\n"

            f"   ✅ {r.get('correct', 0)} درست | "

            f"❌ {r.get('wrong', 0)} غلط | "

            f"⏳ {r.get('blank', 0)} نزده"
        )

    me = next(
        (
            r
            for r in results
            if r.get("uid") == u.id
        ),
        None
    )

    if me:

        lines.append(

            f"\n👤 رتبه شما: "

            f"{results.index(me) + 1}"
        )

    await q.edit_message_text(

        "\n".join(lines),

        reply_markup=kb([
            [
                (
                    "🔙 بازگشت",
                    "exams"
                )
            ]
        ])
    )


# =========================================================
# پردازش متن مدیر
# =========================================================

async def text(
    update,
    ctx
):
    u = update.effective_user

    if not is_admin(u):
        return

    s = states.get(
        u.id
    )

    if not s:
        return

    t = update.message.text.strip()

    a = s["a"]

    if a == "create_name":

        states[u.id] = {
            "a": "create_subtitle",
            "name": t
        }

        await update.message.reply_text(
            "📝 حالا عنوان فرعی را ارسال کنید.\n\n"
            "مثال: درس ۱\n"
            "یا: تست ۱–۱۰۰"
        )

    elif a == "create_subtitle":

        n = 1

        while f"exam_{n}" in exams:
            n += 1

        eid = f"exam_{n}"

        exams[eid] = {

            "id": eid,

            "name": s.get("name", "آزمون"),

            "subtitle": t,

            "questions": [],

            "results": []
        }

        save()

        states.pop(
            u.id,
            None
        )

        await update.message.reply_text(

            f"✅ {exam_full_title(exams[eid])}\n"
            "ساخته شد.",

            reply_markup=admin_list()
        )

    elif a == "rename_name":

        eid = s["eid"]

        if eid not in exams:
            states.pop(u.id, None)
            await update.message.reply_text(
                "❌ آزمون پیدا نشد.",
                reply_markup=admin_list()
            )
            return

        states[u.id] = {
            "a": "rename_subtitle",
            "eid": eid,
            "name": t
        }

        await update.message.reply_text(
            "📝 عنوان فرعی جدید را ارسال کنید.\n\n"
            "مثال: درس ۱\n"
            "یا: تست ۱–۱۰۰"
        )

    elif a == "rename_subtitle":

        eid = s["eid"]

        if eid in exams:
            exams[eid]["name"] = s.get(
                "name",
                exams[eid].get("name", "آزمون")
            )
            exams[eid]["subtitle"] = t
            save()

        states.pop(
            u.id,
            None
        )

        await update.message.reply_text(

            f"✅ عنوان آزمون به‌روزرسانی شد.\n\n"
            f"{exam_full_title(exams[eid])}",

            reply_markup=admin_exam_kb(
                eid
            )
        )

    elif a == "delete":

        eid = s["eid"]

        if t == "حذف":

            exams.pop(
                eid,
                None
            )

            save()

            await update.message.reply_text(

                "🗑 آزمون حذف شد.",

                reply_markup=admin_list()
            )

        else:

            await update.message.reply_text(

                "عملیات لغو شد.",

                reply_markup=admin_list()
            )

        states.pop(
            u.id,
            None
        )


# =========================================================
# انتخاب گروه توسط مدیر
# =========================================================

async def chat_shared(
    update,
    ctx
):
    msg = update.effective_message

    shared = (
        msg.chat_shared
        if msg
        else None
    )

    user = update.effective_user

    if (
        not shared
        or not user
        or not is_admin(user)
    ):
        return

    req = share_requests.pop(
        shared.request_id,
        None
    )

    if (
        not req
        or req.get("uid") != user.id
    ):

        await msg.reply_text(

            "❌ درخواست انتخاب گروه منقضی شده است.",

            reply_markup=ReplyKeyboardRemove()
        )

        return

    eid = req["eid"]

    e = exams.get(
        eid
    )

    if not e or not e.get(
        "questions"
    ):

        await msg.reply_text(

            "❌ این آزمون دیگر در دسترس نیست.",

            reply_markup=ReplyKeyboardRemove()
        )

        return

    missing = [
        i + 1
        for i, question
        in enumerate(
            e["questions"]
        )
        if question.get("answer") is None
    ]

    if missing:

        await msg.reply_text(

            "❌ آزمون قابل ارسال نیست.\n\n"

            "سؤال‌های بدون پاسخ صحیح:\n"

            + ", ".join(
                map(str, missing)
            ),

            reply_markup=ReplyKeyboardRemove()
        )

        return

    chat_id = shared.chat_id

    existing = group_sessions.get(
        chat_id
    )

    if (
        existing
        and existing.get("phase")
        in (
            "register",
            "running"
        )
    ):

        await msg.reply_text(

            "⚠️ در این گروه یک آزمون فعال است.",

            reply_markup=ReplyKeyboardRemove()
        )

        return

    session = ensure_group_session(
        chat_id,
        eid
    )

    if not session:

        await msg.reply_text(

            "❌ نتوانستم آزمون را برای این گروه آماده کنم.",

            reply_markup=ReplyKeyboardRemove()
        )

        return

    try:

        sent = await ctx.bot.send_message(

            chat_id=chat_id,

            text=(

                f"📢 {exam_full_title(e)}\n"
                "آماده ثبت‌نام است.\n\n"

                f"📝 تعداد سؤال: "
                f"{len(e['questions'])}\n"

                f"⏱️ زمان هر سؤال: "
                f"{QUESTION_TIME} ثانیه\n\n"

                "🎯 هر پاسخ صحیح = ۱ امتیاز.\n"

                "❌ هر ۳ پاسخ غلط = کسر ۱ امتیاز.\n\n"

                "⏳ پاسخ ندادن = ۰ امتیاز.\n\n"

                "برای شرکت روی "
                "«اعلام آمادگی» بزنید."
            ),

            reply_markup=group_register_kb(
                eid
            )
        )

        session[
            "announcement_message_id"
        ] = sent.message_id

        await msg.reply_text(

            f"✅ {exam_full_title(e)}\n"
            "به گروه ارسال شد.",

            reply_markup=ReplyKeyboardRemove()
        )

    except Exception as ex:

        group_sessions.pop(
            chat_id,
            None
        )

        await msg.reply_text(

            f"❌ ارسال به گروه انجام نشد.\n{ex}",

            reply_markup=ReplyKeyboardRemove()
        )


# =========================================================
# دریافت TXT
# =========================================================

async def document(
    update,
    ctx
):
    u = update.effective_user

    if not is_admin(u):
        return

    state = states.get(
        u.id
    )

    if (
        not state
        or state.get("a")
        != "upload_wait"
    ):

        await update.message.reply_text(

            "ℹ️ ابتدا از پنل مدیریت گزینه "
            "«📥 ورود فایل TXT» را بزنید."
        )

        return

    d = update.message.document

    if not d.file_name.lower().endswith(
        ".txt"
    ):

        await update.message.reply_text(

            "❌ فقط فایل TXT ارسال کنید."
        )

        return

    f = await d.get_file()

    filename = (
        f"uploaded_questions_"
        f"{u.id}.txt"
    )

    await f.download_to_drive(
        filename
    )

    try:

        with open(
            filename,
            encoding="utf-8-sig"
        ) as z:

            t = z.read()

    except UnicodeDecodeError:

        try:

            with open(
                filename,
                encoding="utf-8"
            ) as z:

                t = z.read()

        except Exception:

            await update.message.reply_text(

                "❌ فایل TXT قابل خواندن نیست."
            )

            return

    qs, errors = parse(t)

    if errors:

        await update.message.reply_text(

            "❌ فایل قابل ورود نیست.\n\n"

            "سؤال‌های زیر مشکل دارند "
            "یا پاسخ صحیح ندارند:\n\n"

            + ", ".join(
                map(str, errors[:50])
            )

            + "\n\n"

            "فرمت صحیح نمونه:\n\n"

            "1. پایتخت ایران کدام است؟\n"
            "A) شیراز\n"
            "B) تهران\n"
            "C) اصفهان\n"
            "D) تبریز\n"
            "ANSWER: B) تهران\n\n"

            "2. پایتخت فرانسه کدام است؟\n"
            "A) برلین\n"
            "B) رم\n"
            "C) پاریس\n"
            "D) مادرید\n"
            "ANSWER: C) پاریس"
        )

        return

    if not qs:

        await update.message.reply_text(

            "❌ هیچ سؤال قابل شناسایی پیدا نشد."
        )

        return

    eid = state.get(
        "eid"
    )

    if eid and eid in exams:

        exams[eid]["questions"] = qs

        save()

        states.pop(
            u.id,
            None
        )

        await update.message.reply_text(

            f"✅ {len(qs)} سؤال به "
            f"«{exam_full_title(exams[eid])}»\nاضافه شد.\n\n"

            "🟢 همه سؤال‌ها پاسخ صحیح دارند.\n"

            "🟢 هر پاسخ صحیح = ۱ امتیاز.\n"

            "🟢 هر ۳ پاسخ غلط = کسر ۱ امتیاز.\n"

            "🟢 آزمون به‌صورت Quiz واقعی تلگرام اجرا می‌شود.",

            reply_markup=admin_exam_kb(
                eid
            )
        )

    else:

        states[u.id] = {

            "a": "upload_select",

            "qs": qs
        }

        await update.message.reply_text(

            f"✅ {len(qs)} سؤال شناسایی شد.\n\n"

            "🟢 همه سؤال‌ها پاسخ صحیح دارند.\n"

            "🟢 هر پاسخ صحیح = ۱ امتیاز.\n"

            "🟢 هر ۳ پاسخ غلط = کسر ۱ امتیاز.\n\n"

            "آزمون مقصد را انتخاب کنید:",

            reply_markup=admin_list()
        )


# =========================================================
# تجزیه TXT
# =========================================================

def parse(t):

    out = []

    errors = []

    t = t.replace(
        "\r\n",
        "\n"
    )

    t = t.replace(
        "\r",
        "\n"
    )

    t = t.replace(
        "\ufeff",
        ""
    )

    t = t.replace(
        "\u200c",
        ""
    )

    # شماره سؤال باید دقیقاً از ابتدای سطر شروع شود.
    #
    # صحیح:
    # 1. سوال اول
    # 2. سوال دوم
    #
    # غلط:
    #    1. سوال اول
    # متن قبل از 1. سوال اول

    matches = list(
        re.finditer(
            r"(?m)^(\d+)[\.\)]\s*",
            t
        )
    )

    if not matches:
        return [], [1]

    for pos, match in enumerate(
        matches
    ):

        number = int(
            match.group(1)
        )

        start = match.end()

        if pos + 1 < len(matches):
            end = matches[
                pos + 1
            ].start()

        else:
            end = len(t)

        block = t[
            start:end
        ].strip()

        if not block:

            errors.append(
                number
            )

            continue

        lines = [
            line.strip()
            for line in block.split("\n")
            if line.strip()
        ]

        question_lines = []

        options_map = {}

        answer = None

        for line in lines:

            # =================================================
            # پاسخ انگلیسی
            # =================================================

            m_answer = re.match(
                r"^\s*ANSWER\s*[:=]\s*(.+?)\s*$",
                line,
                re.IGNORECASE
            )

            if m_answer:

                answer = answer_index(
                    m_answer.group(1).strip()
                )

                continue

            # =================================================
            # پاسخ فارسی
            # =================================================

            m_answer_fa = re.match(
                r"^\s*"
                r"(?:پاسخ|جواب|گزینه\s*صحیح|پاسخ\s*صحیح)"
                r"\s*[:=]\s*(.+?)\s*$",
                line,
                re.IGNORECASE
            )

            if m_answer_fa:

                answer = answer_index(
                    m_answer_fa.group(1).strip()
                )

                continue

            # =================================================
            # گزینه انگلیسی
            # =================================================

            m_en = re.match(
                r"^\s*"
                r"([ABCDabcd])"
                r"\s*[\)\.\:\-]\s*"
                r"(.+?)\s*$",
                line
            )

            if m_en:

                letter = m_en.group(1).upper()

                options_map[letter] = (
                    m_en.group(2).strip()
                )

                continue

            # =================================================
            # گزینه فارسی
            # =================================================

            m_fa = re.match(
                r"^\s*"
                r"(الف|ب|ج|د)"
                r"\s*[\)\.\:\-]\s*"
                r"(.+?)\s*$",
                line
            )

            if m_fa:

                fa_to_en = {
                    "الف": "A",
                    "ب": "B",
                    "ج": "C",
                    "د": "D"
                }

                options_map[
                    fa_to_en[m_fa.group(1)]
                ] = m_fa.group(2).strip()

                continue

            question_lines.append(
                line
            )

        if not all(
            x in options_map
            for x in ["A", "B", "C", "D"]
        ):

            errors.append(
                number
            )

            continue

        if answer is None:

            errors.append(
                number
            )

            continue

        question = " ".join(
            question_lines
        ).strip()

        if not question:

            errors.append(
                number
            )

            continue

        out.append({

            "question": question,

            "options": [
                options_map["A"],
                options_map["B"],
                options_map["C"],
                options_map["D"]
            ],

            "answer": int(answer)
        })

    return out, errors


# =========================================================
# تبدیل پاسخ به شماره گزینه
# =========================================================

def answer_index(ans):

    if ans is None:
        return None

    ans = str(
        ans
    ).strip()

    m = re.match(
        r"^\s*"
        r"(A|B|C|D|الف|ب|ج|د)"
        r"\s*(?:\)|\.|:|-)?",
        ans,
        re.IGNORECASE
    )

    if m:

        letter = m.group(1).lower()

        mapping = {
            "a": 0,
            "b": 1,
            "c": 2,
            "d": 3,

            "الف": 0,
            "ب": 1,
            "ج": 2,
            "د": 3
        }

        return mapping.get(
            letter
        )

    if ans in (
        "1",
        "2",
        "3",
        "4"
    ):

        return int(ans) - 1

    return None


# =========================================================
# انتخاب آزمون بعد از آپلود
# =========================================================

async def buttons2(
    update,
    ctx
):
    q = update.callback_query

    u = q.from_user

    s = states.get(
        u.id
    )

    if (
        s
        and s.get("a")
        == "upload_select"
        and q.data.startswith("ae:")
    ):

        await q.answer()

        eid = q.data[3:]

        if eid in exams:

            exams[eid]["questions"] = (
                s["qs"]
            )

            save()

            count = len(
                s["qs"]
            )

            states.pop(
                u.id,
                None
            )

            await q.edit_message_text(

                f"✅ {count} سؤال به "
                f"«{exam_full_title(exams[eid])}»\nاضافه شد.\n\n"

                "🟢 آزمون با Quiz واقعی تلگرام اجرا می‌شود.\n"

                "🟢 هر پاسخ صحیح = ۱ امتیاز.\n"

                "🟢 هر ۳ پاسخ غلط = کسر ۱ امتیاز.",

                reply_markup=admin_exam_kb(
                    eid
                )
            )

        return

    await buttons(
        update,
        ctx
    )


# =========================================================
# اجرای ربات
# =========================================================

def main():

    token = os.getenv(
        "BOT_TOKEN"
    )

    if not token:

        print(
            "❌ BOT_TOKEN تنظیم نشده است."
        )

        return

    load()

    request = HTTPXRequest(
        proxy=PROXY
    )

    app = (
        Application
        .builder()
        .token(token)
        .request(request)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "panel",
            panel
        )
    )

    app.add_handler(
        PollAnswerHandler(
            polls_answer
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            buttons2
        )
    )

    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.CHAT_SHARED,
            chat_shared
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            document
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text
        )
    )

    print(
        "🤖 Bot is running..."
    )

    app.run_polling()


# =========================================================
# شروع
# =========================================================

if __name__ == "__main__":
    main()