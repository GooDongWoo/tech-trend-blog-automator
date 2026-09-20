import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from src.profiler.interest_profiler import InterestProfiler
from src.collector.orchestrator import TrendOrchestrator
from src.curator.matcher import TrendMatcher, CuratedTopic
from src.writer.blog_writer import BlogWriter
from src.publisher.git_publisher import GitPublisher
from src.publisher.obsidian_sync import ObsidianSync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TrendBotApp:
    """Telegram Bot application managing daily briefings, user selection, and blog publishing."""

    def __init__(self):
        self.profiler = InterestProfiler()
        self.collector = TrendOrchestrator()
        self.matcher = TrendMatcher()
        self.writer = BlogWriter()
        self.publisher = GitPublisher()
        self.obsidian_sync = ObsidianSync()
        self.scheduler = AsyncIOScheduler()

        # Cache of current curated topics
        self.current_topics: Dict[int, CuratedTopic] = {}
        # Cache of last drafted post
        self.last_draft: Optional[Dict[str, Any]] = None

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        chat_id = update.effective_chat.id
        logger.info(f"User connected: chat_id={chat_id}")
        await update.message.reply_text(
            f"👋 안녕하세요 동우님! 기술 트렌드 큐레이터 & 블로그 자동화 봇입니다.\n\n"
            f"📌 등록된 Chat ID: `{chat_id}`\n"
            f"⏰ 매일 지정 시각({settings.schedule_time})에 맞춤형 트렌드 5선이 도착합니다.\n\n"
            f"💡 **사용 가능한 명령어**:\n"
            f"- `/now` 또는 `/trend`: 지금 바로 오늘의 트렌드 5선 받아보기\n"
            f"- `/status`: 시스템 및 연결 상태 점검\n",
            parse_mode=ParseMode.MARKDOWN
        )

    async def trigger_briefing(self, chat_id: Optional[str] = None, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
        """Fetch interests, collect trends, curate Top 5, and send briefing card."""
        target_chat_id = chat_id or settings.telegram_chat_id
        if not target_chat_id:
            logger.warning("No target chat_id configured.")
            return

        bot = context.bot if context else None

        # 1. Profile user interests
        profile = self.profiler.build_profile()

        # 2. Collect trends
        raw_items = await self.collector.collect_all(limit_per_source=8)

        # 3. Curate Top 5
        curated = self.matcher.curate_top_5(profile, raw_items)
        self.current_topics = {t.rank: t for t in curated}

        # 4. Format Message & Keyboard
        card_text = self.matcher.format_telegram_card(curated)

        keyboard = [
            [
                InlineKeyboardButton(f"{t.rank}번 선택", callback_data=f"select_{t.rank}")
                for t in curated[:3]
            ],
            [
                InlineKeyboardButton(f"{t.rank}번 선택", callback_data=f"select_{t.rank}")
                for t in curated[3:]
            ] + [InlineKeyboardButton("🔄 새로고침", callback_data="refresh_topics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if not bot and settings.telegram_bot_token:
            from telegram import Bot
            bot = Bot(token=settings.telegram_bot_token)

        if bot:
            try:
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=card_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
            except Exception as e:
                logger.warning(f"Markdown send failed ({e}), falling back to plain text.")
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=card_text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )


    async def now_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /now command."""
        await update.message.reply_text("🔍 최근 Obsidian 관심사를 분석하고 최신 트렌드를 수집하고 있습니다. 잠시만 기다려주세요...")
        await self.trigger_briefing(chat_id=str(update.effective_chat.id), context=context)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button clicks."""
        query = update.callback_query
        await query.answer()

        data = query.data
        if data == "refresh_topics":
            await query.edit_message_text("🔄 트렌드를 다시 수집하고 큐레이션합니다...")
            await self.trigger_briefing(chat_id=str(query.message.chat_id), context=context)
            return

        if data.startswith("select_"):
            rank = int(data.replace("select_", ""))
            topic = self.current_topics.get(rank)
            if not topic:
                await query.edit_message_text("❌ 선택한 주제 정보를 찾을 수 없습니다. 다시 시도해주세요.")
                return

            await query.edit_message_text(
                f"✅ **[{topic.rank}번 채택]** {topic.title}\n\n"
                f"🔍 공식 문서 및 관련 자료 심층 리서치에 착수합니다...\n"
                f"✍️ 동우님 스타일의 유쾌한 평어체 블로그 초안을 작성 중입니다. (약 30~60초 소요)",
                parse_mode=ParseMode.MARKDOWN
            )

            # Asynchronously write blog post
            draft_result = await self.writer.generate_post(topic)
            self.last_draft = draft_result

            # Send preview and approval buttons
            preview_msg = (
                f"🎉 **블로그 글 초안이 완성되었습니다!**\n\n"
                f"📄 **제목**: {draft_result['title']}\n"
                f"📁 **저장 위치**: `{draft_result['relative_path']}`\n\n"
                f"**[본문 미리보기 (일부)]**\n"
                f"```markdown\n{draft_result['content'][:500]}...\n```\n\n"
                f"배포 승인을 누르면 GitHub Pages(`GooDongWoo.github.io`)에 자동 푸시되고, "
                f"Obsidian 볼트에도 지식 노트가 생성 및 연결됩니다."
            )
            approval_keyboard = [
                [
                    InlineKeyboardButton("🚀 배포 승인 (Push)", callback_data="approve_push"),
                    InlineKeyboardButton("🔄 재작성 (Retry)", callback_data=f"select_{rank}"),
                ],
                [InlineKeyboardButton("❌ 취소", callback_data="cancel_draft")]
            ]
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=preview_msg,
                reply_markup=InlineKeyboardMarkup(approval_keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if data == "approve_push":
            if not self.last_draft:
                await query.edit_message_text("❌ 배포할 초안 정보를 찾을 수 없습니다.")
                return

            await query.edit_message_text("🚀 GitHub Pages에 푸시하고 Obsidian 볼트와 동기화 중입니다...")

            # 1. Git Commit & Push
            push_res = self.publisher.publish(self.last_draft["file_path"], self.last_draft["title"])

            # 2. Obsidian Sync
            obs_res = self.obsidian_sync.sync_post(self.last_draft)

            live_url = f"{settings.blog_base_url.rstrip('/')}/{self.last_draft['slug']}/"
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"✨ **성공적으로 배포 및 동기화되었습니다!** ✨\n\n"
                    f"🌐 **블로그 주소**: {live_url}\n"
                    f"📝 **Git 결과**: {push_res.get('message')}\n"
                    f"📓 **Obsidian 노트**: `{obs_res.get('note_path')}` (일기 위키링크 연결 완료!)\n"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if data == "cancel_draft":
            await query.edit_message_text("🚫 배포가 취소되었습니다. 작성된 초안 파일은 로컬에 보존됩니다.")

    def run(self):
        """Start the Telegram bot and background scheduler."""
        token = settings.telegram_bot_token
        if not token or token == "your_telegram_bot_token_here":
            logger.warning("No valid TELEGRAM_BOT_TOKEN found in config/.env. Bot cannot start.")
            return

        app = Application.builder().token(token).build()

        app.add_handler(CommandHandler("start", self.start_cmd))
        app.add_handler(CommandHandler("now", self.now_cmd))
        app.add_handler(CommandHandler("trend", self.now_cmd))
        app.add_handler(CallbackQueryHandler(self.handle_callback))

        # Setup scheduler
        try:
            hour, minute = settings.schedule_time.split(":")
            self.scheduler.add_job(
                self.trigger_briefing,
                "cron",
                hour=int(hour),
                minute=int(minute),
                args=[settings.telegram_chat_id, app]
            )
            self.scheduler.start()
            logger.info(f"Scheduler started. Daily briefing scheduled at {settings.schedule_time}")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

        logger.info("Telegram Bot is polling...")
        app.run_polling()
