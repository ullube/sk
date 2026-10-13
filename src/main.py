import pytz
import asyncio
import sys
import aiosqlite
from pathlib import Path
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta

# Loyiha yo'lini sozlash
sys.path.append(str(Path(__file__).resolve().parent.parent))

from config import BOT_TOKEN
from src.handlers import hd
from src.handlers import hd_admin
from src.handlers import hd_workplace
from src.database import db
import src.keyboards.kbd as kbd
from aiogram.types import BotCommand

async def set_bot_commands(bot: Bot):
    """Chap menyu commandalar"""
    commands = [
        BotCommand(command="mytable", description="📊 내 근무표"),
        BotCommand(command="info", description="👤 프로필"),
        BotCommand(command="monthly", description="📅 월별 전체 보기")
    ]
    await bot.set_my_commands(commands)

async def send_morning_reminder(bot: Bot):
    """Har daqiqada ishga tushadi (Seoul vaqti bilan) va har bir foydalanuvchining
    PROFIL bo'limida o'zi belgilagan vaqtida eslatma yuboradi (⏰ 알림 시간 수정).

    Agar shu kunda oldindan jadval bo'yicha ma'lumot to'ldirilgan bo'lsa,
    faqat tasdiqlashni so'raydi. Aks holda eskisidek soatni so'raydi.
    Har bir userga kuniga faqat bitta marta yuboriladi (last_reminder_date orqali)."""
    try:
        seoul_tz = pytz.timezone("Asia/Seoul")
        now = datetime.now(seoul_tz)
        current_hm = now.strftime("%H:%M")
        today_str = now.strftime("%Y-%m-%d")

        yesterday = now - timedelta(days=1)
        yesterday_str = f"{yesterday.month}월 {yesterday.day}일"
        yesterday_date = yesterday.strftime("%Y-%m-%d")

        users = await db.get_all_users_with_reminder_time()

        for user_id, reminder_time, last_sent in users:
            reminder_time = reminder_time or "05:00"

            # Foydalanuvchining o'z vaqti hozirgi daqiqaga to'g'ri kelmasa - o'tkazib yuboramiz
            if reminder_time != current_hm:
                continue

            # Bugun allaqachon yuborilgan bo'lsa - qayta yubormaymiz
            if last_sent == today_str:
                continue

            try:
                workplaces = await db.get_user_workplaces(user_id)

                if not workplaces:
                    # Ishxona yo'q bo'lsa - eski usulda so'ramaymiz, o'tkazib yuboramiz
                    await db.set_last_reminder_date(user_id, today_str)
                    continue

                workplace_id = workplaces[0][0]

                # Jadval bo'yicha oldindan to'ldirilgan ma'lumot bormi tekshirish
                existing_hours = await db.get_log_hours(user_id, workplace_id, yesterday_date)

                if existing_hours is not None:
                    # Ma'lumot allaqachon mavjud - faqat tasdiqlashni so'raymiz
                    if existing_hours == 0:
                        status_text = "🏖 휴무로 등록되어 있습니다"
                    else:
                        status_text = f"{existing_hours}시간 근무로 등록되어 있습니다"

                    text = (
                        f"☀️ 좋은 아침입니다!\n\n"
                        f"어제 ({yesterday_str}) 근무표에 따르면 {status_text}.\n\n"
                        f"맞습니까?"
                    )
                    await bot.send_message(
                        user_id,
                        text,
                        reply_markup=kbd.daily_confirm_inline(workplace_id, yesterday_date)
                    )
                else:
                    # Ma'lumot yo'q - eski usulda so'raymiz
                    await bot.send_message(
                        user_id,
                        f"☀️ 좋은 아침입니다!\n\n어제 ({yesterday_str}) 근무 시간을 기록해주세요:",
                        reply_markup=kbd.daily_report_inline()
                    )

                await db.set_last_reminder_date(user_id, today_str)
                print(f"[REMINDER] Xabar yuborildi: user_id={user_id} vaqt={reminder_time}")
            except Exception as e:
                print(f"[ERROR] Foydalanuvchiga yuborib bo'lmadi user_id={user_id}: {e}")
                continue

    except Exception as e:
        print(f"[ERROR] Eslatma yuborishda xato: {e}")

async def main():
    # Ma'lumotlar bazasini ishga tushirish
    await db.init_db()

    # Botni sozlash
    bot = Bot(token=BOT_TOKEN)

    # Chap menu commandalar
    await set_bot_commands(bot)

    dp = Dispatcher()
    dp.include_router(hd_admin.admin_router)  # Admin router birinchi
    dp.include_router(hd_workplace.router)  # Yangi workplace router
    dp.include_router(hd.router)  # Eski router (fallback)

    # Scheduler (Koreya vaqti bilan) - HAR DAQIQADA tekshiradi, chunki endi
    # har bir foydalanuvchi o'z eslatma vaqtini o'zi belgilaydi (profilda)
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    scheduler.add_job(send_morning_reminder, "cron", minute="*", args=[bot])
    scheduler.start()

    print("=" * 50)
    print("🤖 봇이 시작되었습니다 (Korea Time Zone)")
    print("=" * 50)
    print(f"📅 현재 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏰ 알림: 각 사용자가 프로필에서 설정한 시간에 개별 발송 (매분 확인)")
    print("=" * 50)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[STOP] Bot to'xtatildi")