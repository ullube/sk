from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import calendar
from src.database import db
from src.keyboards import kbd
from src.utils.state_filters import is_free_text
from aiogram.types import BufferedInputFile
from src.utils.image_generator import create_calendar_image
import asyncio
import aiosqlite

router = Router()

class AddWorkplaceForm(StatesGroup):
    name = State()

# ===== HELPER FUNKSIYA - XATOLARNI OLDINI OLISH =====
async def safe_edit_or_answer(callback, text, reply_markup=None, parse_mode=None):
    """Xabarni tahrirlash yoki yangi yuborish (rasm xatoligini oldini oladi)"""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except:
        # Agar xabar rasm bo'lsa yoki boshqa xato bo'lsa
        try:
            await callback.message.delete()
        except:
            pass
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)

# Yordamchi funksiya - xabarni o'chirish
async def delete_message_after(bot, chat_id, message_id, seconds):
    """Xabarni berilgan soniyadan keyin o'chirish"""
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

# ===== YANGI: Umumiy yordamchi - berilgan oy uchun kalendar rasmini yaratish =====
async def build_month_report_image(user_id, workplace_id, year, month, first_name=None, last_name=None):
    """Berilgan oy uchun hisobot rasmini qaytaradi.
    Eski yozuvlar workplace_id dan qat'i nazar bazadan to'liq olinadi (user_id + sana bo'yicha).
    Hali kelmagan oylar uchun yozuvi yo'q kunlar haftalik jadval bo'yicha hisoblanadi."""
    workplace_name = await db.get_workplace_name(workplace_id)

    # O'ZGARDI: workplace_id bo'yicha filtr olib tashlandi
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute(
            "SELECT work_date, hours, note FROM work_logs "
            "WHERE user_id = ? AND work_date LIKE ? ORDER BY work_date",
            (user_id, f"{year}-{month:02d}%")
        ) as cursor:
            logs = await cursor.fetchall()

    work_dict = {log[0]: (log[1] or 0) for log in logs}
    notes_dict = {log[0]: log[2] for log in logs if len(log) > 2 and log[2]}

    # Kelajakdagi oylar uchun jadval bo'yicha avtomatik hisob (bazaga yozilmaydi)
    now = datetime.now()
    if (year, month) > (now.year, now.month):
        schedule = await db.get_user_schedule(user_id)
        if schedule:
            days_in_month = calendar.monthrange(year, month)[1]
            for day in range(1, days_in_month + 1):
                date_obj = datetime(year, month, day)
                date_key = date_obj.strftime("%Y-%m-%d")
                if date_key in work_dict:
                    continue  # qo'lda kiritilgan yozuv ustun turadi
                day_name = db.WEEKDAY_NAMES[date_obj.weekday()]
                work_dict[date_key] = schedule.get(day_name, 0)

    hourly_rate, tax_rate = await db.get_user_settings(user_id)
    total_hours = sum([h for h in work_dict.values() if h > 0])
    gross = total_hours * hourly_rate
    tax = gross * (tax_rate / 100)
    net = gross - tax

    image = await create_calendar_image(
        workplace_name, month, year,
        work_dict, total_hours, gross, tax, net,
        hourly_rate, tax_rate,
        first_name=first_name,
        last_name=last_name,
        notes_dict=notes_dict
    )

    photo = BufferedInputFile(image.read(), filename="calendar.png")
    return photo, workplace_name
# ===== YANGI: ishxona shu foydalanuvchiga tegishlimi =====
async def user_owns_workplace(user_id, workplace_id) -> bool:
    workplaces = await db.get_user_workplaces(user_id)
    return any(wp[0] == workplace_id for wp in workplaces)

# /start command
@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()

    await db.update_user_info(
        user_id,
        message.from_user.full_name,
        message.from_user.username
    )

    if not await db.is_user_active(user_id):
        await message.answer("🚫 차단된 사용자입니다.\n관리자에게 문의하세요.")
        return

    await message.answer(
        "원하시는 메뉴를 선택해주세요:",
        reply_markup=kbd.main_menu_inline()
    )

# ===== YANGI: /mytable - chap menyudagi "📊 내 근무표" tugmasi (bitta bosishda to'g'ridan-to'g'ri ochiladi) =====
@router.message(F.text == "/mytable")
async def cmd_my_table(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()

    await db.update_user_info(
        user_id,
        message.from_user.full_name,
        message.from_user.username
    )

    if not await db.is_user_active(user_id):
        await message.answer("🚫 차단된 사용자입니다.\n관리자에게 문의하세요.")
        return

    workplaces = await db.get_user_workplaces(user_id)

    if not workplaces:
        await message.answer(
            "🏢 직장이 없습니다.\n먼저 직장을 추가하세요:",
            reply_markup=kbd.add_workplace_only_inline()
        )
        return

    # Bitta ishxona bo'lsa - to'g'ridan-to'g'ri jadval rasmi
    if len(workplaces) == 1:
        workplace_id = workplaces[0][0]
        now = datetime.now()
        photo, workplace_name = await build_month_report_image(
            user_id, workplace_id, now.year, now.month,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )
        sent_message = await message.answer_photo(
            photo=photo,
            caption=f"🏢 {workplace_name}\n📅 {now.year}년 {now.month}월 근무표",
            reply_markup=kbd.workplace_actions_inline(workplace_id)
        )
        # 1 daqiqadan keyin rasmni o'chirish
        asyncio.create_task(delete_message_after(message.bot, sent_message.chat.id, sent_message.message_id, 60))
        return

    await message.answer(
        "🏢 직장을 선택하세요:",
        reply_markup=kbd.workplaces_list_inline(workplaces)
    )

# Asosiy menyuga qaytish
@router.callback_query(F.data == "main_menu")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit_or_answer(
        callback,
        "원하시는 메뉴를 선택해주세요:",
        reply_markup=kbd.main_menu_inline()
    )

# 내 근무표 - ISHXONALAR RO'YXATI (yoki 1 ta bo'lsa to'g'ridan-to'g'ri hisobot)
@router.callback_query(F.data == "my_workplaces")
async def show_workplaces_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id

    workplaces = await db.get_user_workplaces(user_id)

    if not workplaces:
        await safe_edit_or_answer(
            callback,
            "🏢 직장이 없습니다.\n먼저 직장을 추가하세요:",
            reply_markup=kbd.add_workplace_only_inline()
        )
        return

    # YANGI: bitta ishxona bo'lsa - tanlash bosqichini o'tkazib yuborish
    if len(workplaces) == 1:
        await show_workplace_report(callback, workplace_id=workplaces[0][0])
        return

    # Ishxonalar ro'yxati
    await safe_edit_or_answer(
        callback,
        "🏢 직장을 선택하세요:",
        reply_markup=kbd.workplaces_list_inline(workplaces)
    )

# Ishxona tanlash - MA'LUMOT KO'RSATISH
@router.callback_query(F.data.startswith("select_workplace_"))
async def show_workplace_report(callback: CallbackQuery, workplace_id: int = None):
    await callback.answer()
    if workplace_id is None:
        workplace_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    now = datetime.now()
    year = now.year
    month = now.month

    # Rasm yaratish (eslatmalar bilan birga)
    photo, workplace_name = await build_month_report_image(
        user_id, workplace_id, year, month,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name
    )

    # Eski xabarni o'chirish
    try:
        await callback.message.delete()
    except:
        pass

    # Rasmni yuborish
    sent_message = await callback.message.answer_photo(
        photo=photo,
        caption=f"🏢 {workplace_name}\n📅 {year}년 {month}월 근무표",
        reply_markup=kbd.workplace_actions_inline(workplace_id)
    )

    # 1 daqiqadan keyin rasmni o'chirish
    asyncio.create_task(delete_message_after(callback.message.bot, sent_message.chat.id, sent_message.message_id, 60))

# Yangi ishxona qo'shish
@router.callback_query(F.data == "add_new_workplace")
async def add_workplace_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("🏢 새 직장 이름을 입력하세요:")
    await state.set_state(AddWorkplaceForm.name)

@router.message(AddWorkplaceForm.name, is_free_text)
async def add_workplace_finish(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text.strip()

    await db.add_workplace(user_id, name)
    await state.clear()

    workplaces = await db.get_user_workplaces(user_id)
    await message.answer(
        f"✅ '{name}' 직장이 추가되었습니다!",
        reply_markup=kbd.workplaces_list_inline(workplaces)
    )

# Ishxonani o'chirish - ro'yxat ko'rsatish
@router.callback_query(F.data == "remove_workplace_list")
async def show_remove_workplace_list(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    workplaces = await db.get_user_workplaces(user_id)

    if not workplaces:
        await safe_edit_or_answer(
            callback,
            "🏢 직장이 없습니다.",
            reply_markup=kbd.back_to_main_inline()
        )
        return

    await safe_edit_or_answer(
        callback,
        "🗑 삭제할 직장을 선택하세요:\n\n⚠️ 직장을 삭제하면 해당 직장의 모든 근무 기록도 함께 삭제됩니다!",
        reply_markup=kbd.workplaces_remove_inline(workplaces)
    )

# Ishxonani o'chirishni tasdiqlash
@router.callback_query(F.data.startswith("confirm_remove_wp_"))
async def confirm_remove_workplace(callback: CallbackQuery):
    await callback.answer()
    workplace_id = int(callback.data.split("_")[-1])
    workplace_name = await db.get_workplace_name(workplace_id)

    await safe_edit_or_answer(
        callback,
        f"⚠️ '{workplace_name}' 직장을 삭제하시겠습니까?\n\n이 직장의 모든 근무 기록이 함께 삭제됩니다!",
        reply_markup=kbd.confirm_remove_workplace_inline(workplace_id)
    )

# Ishxonani o'chirish
@router.callback_query(F.data.startswith("delete_wp_"))
async def delete_workplace_confirmed(callback: CallbackQuery):
    await callback.answer()
    workplace_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id

    workplace_name = await db.get_workplace_name(workplace_id)
    await db.delete_workplace(workplace_id)

    workplaces = await db.get_user_workplaces(user_id)

    if not workplaces:
        await safe_edit_or_answer(
            callback,
            f"✅ '{workplace_name}' 직장이 삭제되었습니다.\n\n🏢 직장이 없습니다.\n먼저 직장을 추가하세요:",
            reply_markup=kbd.add_workplace_only_inline()
        )
    else:
        await safe_edit_or_answer(
            callback,
            f"✅ '{workplace_name}' 직장이 삭제되었습니다.\n\n🏢 직장을 선택하세요:",
            reply_markup=kbd.workplaces_list_inline(workplaces)
        )

# ===================== YANGI: 월별 전체 보기 (rasmsiz, bitta matnli xabar) =====================

@router.callback_query(F.data == "monthly_overview")
async def monthly_overview_workplaces(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    workplaces = await db.get_user_workplaces(user_id)

    if not workplaces:
        await safe_edit_or_answer(
            callback,
            "🏢 직장이 없습니다.",
            reply_markup=kbd.back_to_main_inline()
        )
        return

    # Bitta ishxona bo'lsa - to'g'ridan-to'g'ri to'liq tarixni ko'rsatish
    if len(workplaces) == 1:
        await show_full_monthly_summary(callback, workplaces[0][0])
        return

    await safe_edit_or_answer(
        callback,
        "🏢 직장을 선택하세요:",
        reply_markup=kbd.workplaces_for_monthly_inline(workplaces)
    )

# Ishxona tanlash (월별 uchun) - to'g'ridan-to'g'ri to'liq tarix
@router.callback_query(F.data.startswith("monthly_wp_"))
async def select_month_for_workplace(callback: CallbackQuery):
    await callback.answer()
    workplace_id = int(callback.data.split("_")[-1])
    await show_full_monthly_summary(callback, workplace_id)

async def show_full_monthly_summary(callback: CallbackQuery, workplace_id: int):
    """Barcha oylarning umumiy hisobotini bitta chiroyli matnli xabarda ko'rsatish"""
    user_id = callback.from_user.id
    text = await build_full_monthly_text(user_id, workplace_id)
    # YANGI: 📅 캘린더 tugmasi bilan
    await safe_edit_or_answer(callback, text, reply_markup=kbd.monthly_summary_inline(workplace_id))

async def build_full_monthly_text(user_id: int, workplace_id: int) -> str:
    """Bazadagi barcha oylar hisoboti (workplace_id dan qat'i nazar, user_id bo'yicha)"""
    workplace_name = await db.get_workplace_name(workplace_id)
    hourly_rate, tax_rate = await db.get_user_settings(user_id)

    # O'ZGARDI: workplace_id bo'yicha filtr olib tashlandi
    async with aiosqlite.connect(db.DB_PATH) as conn:
        async with conn.execute("""
            SELECT substr(work_date, 1, 7) as ym,
                   SUM(CASE WHEN hours > 0 THEN hours ELSE 0 END) as total_hours,
                   SUM(CASE WHEN hours > 0 THEN 1 ELSE 0 END) as work_days_count
            FROM work_logs
            WHERE user_id = ?
            GROUP BY ym
            ORDER BY ym DESC
        """, (user_id,)) as cursor:
            summaries = await cursor.fetchall()

    if not summaries:
        return f"🏢 {workplace_name}\n\n📭 근무 기록이 없습니다."

    lines = [f"🏢 {workplace_name}", "📊 전체 근무 내역", "━━━━━━━━━━━━━━━━━━━━"]

    total_all_hours = 0
    total_all_net = 0

    for ym, total_hours, work_days_count in summaries:
        year, month = ym.split('-')
        gross = total_hours * hourly_rate
        tax = gross * (tax_rate / 100)
        net = gross - tax

        total_all_hours += total_hours
        total_all_net += net

        lines.append(f"\n🗓 {year}년 {int(month)}월")
        lines.append(f"　근무일 {work_days_count}일 · ⏱ {total_hours}시간")
        lines.append(f"　📉 세금 {tax:,.0f}원")
        lines.append(f"　💵 실수령 {net:,.0f}원")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📌 총 근무시간: {total_all_hours}시간")
    lines.append(f"📌 총 실수령(누적): {total_all_net:,.0f}원")

    text = "\n".join(lines)

    # Telegram xabar chegarasi (~4096 belgi)
    if len(text) > 4000:
        text = text[:3950] + "\n\n… (내역이 많아 일부만 표시됩니다)"

    return text

# YANGI: /monthly komandasi orqali (chap menyu / matn buyrug'i) kirish
@router.message(F.text.in_(["/monthly", "월별 전체 보기"]))
async def monthly_overview_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()
    workplaces = await db.get_user_workplaces(user_id)

    if not workplaces:
        await message.answer("🏢 직장이 없습니다.", reply_markup=kbd.back_to_main_inline())
        return

    if len(workplaces) == 1:
        text = await build_full_monthly_text(user_id, workplaces[0][0])
        # YANGI: 📅 캘린더 tugmasi bilan
        await message.answer(text, reply_markup=kbd.monthly_summary_inline(workplaces[0][0]))
        return

    await message.answer("🏢 직장을 선택하세요:", reply_markup=kbd.workplaces_for_monthly_inline(workplaces))

# ===================== YANGI: 📅 캘린더 - yil tanlash va istalgan oyni ko'rish =====================

# Tanlangan yilning 12 oyini ko'rsatish
@router.callback_query(F.data.startswith("mcal_"))
async def show_year_months(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    workplace_id = int(parts[1])
    year = int(parts[2])
    user_id = callback.from_user.id

    # Yil oralig'ini cheklaymiz (xato sana chiqmasligi uchun)
    if year < 2000 or year > 2100:
        return

    if not await user_owns_workplace(user_id, workplace_id):
        return

    workplace_name = await db.get_workplace_name(workplace_id)

    await safe_edit_or_answer(
        callback,
        f"🏢 {workplace_name}\n📅 {year}년\n\n확인할 월을 선택하세요:",
        reply_markup=kbd.year_months_inline(workplace_id, year)
    )

# Tanlangan oyning to'liq hisoboti (rasm: soat, oylik, soliq, memo)
@router.callback_query(F.data.startswith("mrep_"))
async def show_selected_month_report(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    workplace_id = int(parts[1])
    year = int(parts[2])
    month = int(parts[3])
    user_id = callback.from_user.id

    if month < 1 or month > 12 or year < 2000 or year > 2100:
        return

    if not await user_owns_workplace(user_id, workplace_id):
        return

    photo, workplace_name = await build_month_report_image(
        user_id, workplace_id, year, month,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name
    )

    # Eski xabarni o'chirish
    try:
        await callback.message.delete()
    except:
        pass

    # Bu yerda rasm avtomatik o'chirilmaydi - foydalanuvchi oylar orasida erkin yuradi
    await callback.message.answer_photo(
        photo=photo,
        caption=f"🏢 {workplace_name}\n📅 {year}년 {month}월 근무표",
        reply_markup=kbd.month_report_back_inline(workplace_id, year)
    )