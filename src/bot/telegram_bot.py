import asyncio
import logging
from typing import Dict, Optional
import json
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
from src.editorial.pipeline import EditorialPipeline, approval_callback, decode_callback, topic_callback, decode_topic_callback
from src.editorial.models import DraftStatus
from src.publisher.git_publisher import GitPublisher
from src.publisher.obsidian_sync import ObsidianSync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TrendBotApp:
    """Telegram Bot application managing daily briefings, user selection, and blog publishing."""

    def __init__(self, *, pipeline: EditorialPipeline | None = None, publisher=None, sync=None):
        self.profiler = InterestProfiler()
        self.collector = TrendOrchestrator()
        self.matcher = TrendMatcher()
        self.pipeline = pipeline or EditorialPipeline()
        self.publisher = publisher or GitPublisher()
        self.sync = sync or ObsidianSync()
        self.scheduler = AsyncIOScheduler()

        # Cache of current curated topics
        self.current_topics: Dict[int, CuratedTopic] = {}

    def publication_block_reason(self):
        if settings.editorial_shadow_mode:
            return "shadow mode: 전체 초안·보고서를 검토할 수 있으며 발행은 차단됩니다."
        if not settings.editorial_cutover_authorized:
            return "cutover authorization required: 별도 운영 전환 승인이 필요합니다."
        try:
            from scripts.evaluate_drafts import load_json, release_gate
            report = load_json(settings.editorial_quality_gate_report)
            gate = release_gate(report["runs"], report["human_reviews"], set(report["required_topic_types"]))
            if not gate["eligible"]:
                return "quality gate blocked: " + ", ".join(gate["reasons"])
        except (OSError, ValueError, KeyError, TypeError):
            return "quality gate report unavailable or invalid"
        return None

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
        topic_ids = {t.rank: self.pipeline.register_topic(t, user_context=profile.user_context) for t in curated}

        # 4. Format Message & Keyboard
        card_text = self.matcher.format_telegram_card(curated)

        keyboard = [
            [
                InlineKeyboardButton(f"{t.rank}번 선택", callback_data=topic_callback(topic_ids[t.rank]))
                for t in curated[:3]
            ],
            [
                InlineKeyboardButton(f"{t.rank}번 선택", callback_data=topic_callback(topic_ids[t.rank]))
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

    def reviewer_identity(self):
        chat = str(settings.telegram_chat_id)
        # Private chat IDs are user IDs. Groups need an explicit reviewer user.
        user = str(settings.telegram_reviewer_user_id or (chat if chat.isdigit() else ""))
        if not chat or not user:
            raise ValueError("Telegram reviewer chat/user is not configured")
        return chat, user

    async def send_review(self, bot, chat_id, artifact):
        chat, user = self.reviewer_identity()
        if str(chat_id) != chat:
            raise ValueError("unauthorized review chat")
        directory = artifact.content_path.parent
        # Full files are delivered before any approval button is offered.
        for path in (artifact.content_path, directory / "review.md", *artifact.media_paths):
            if path == artifact.content_path and path.stat().st_size == 0:
                continue  # Blocked generation can legitimately have no article body.
            with path.open("rb") as document:
                await bot.send_document(chat_id=chat_id, document=document, filename=path.name)
        self.pipeline.bind_reviewer(artifact.id, chat, user)
        packet = json.loads(artifact.evidence_path.read_text(encoding="utf-8"))
        sources = (packet or {}).get("sources", [])
        report = json.loads(artifact.report_path.read_text(encoding="utf-8"))
        issues = report.get("reasons", [])
        warnings = report.get("warnings", [])
        rows = [[InlineKeyboardButton(source["title"][:80], url=source["url"])]
                for source in sources if source["url"].startswith(("https://", "http://"))]
        if artifact.status == DraftStatus.REVIEW_READY:
            rows.append([InlineKeyboardButton("전체 검토 후 승인", callback_data=approval_callback(artifact))])
        rows.append([InlineKeyboardButton("새 수정본 생성", callback_data=f"r:{artifact.id}")])
        text = (f"초안 상태: {artifact.status.value}\nID: {artifact.id}\n"
                f"전체 초안: {artifact.content_path}\n근거·검증 보고서: {directory / 'review.md'}\n"
                f"SHA-256: {artifact.content_sha256}\n원문: {len(sources)}개\n"
                f"미해결 사항: {', '.join(issues)[:800] or '없음'}\n"
                f"검토 참고: {', '.join(warnings)[:500] or '없음'}\n"
                "첨부한 전체 본문과 근거 보고서를 확인하세요. 승인은 이 초안에만 기록됩니다.")
        if reason := self.publication_block_reason():
            text += "\n" + reason
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(rows),
                               disable_web_page_preview=True)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        try:
            chat, user = self.reviewer_identity()
            if str(query.message.chat_id) != chat or str(getattr(query.from_user, "id", "")) != user:
                raise ValueError("unauthorized reviewer")
        except (ValueError, AttributeError) as error:
            await query.answer(f"처리 차단: {error}", show_alert=True)
            return
        await query.answer()
        try:
            data = query.data or ""
            if data == "refresh_topics":
                await self.trigger_briefing(chat_id=chat, context=context)
            elif data.startswith("select_"):
                raise ValueError("obsolete topic selection; refresh the briefing to bind its input")
            elif data.startswith("s:"):
                topic_id = decode_topic_callback(data)
                await query.edit_message_text("원문과 근거를 확인해 로컬 검토 초안을 생성합니다.")
                artifact = await self.pipeline.generate(topic_id)
                await self.send_review(context.bot, query.message.chat_id, artifact)
            elif data.startswith("r:"):
                draft_id = data.removeprefix("r:")
                self.pipeline.check_reviewer(draft_id, chat, user)
                artifact = await self.pipeline.retry(draft_id)
                await self.send_review(context.bot, query.message.chat_id, artifact)
            elif data.startswith("a:"):
                draft_id, expected_hash = decode_callback(data)
                self.pipeline.check_reviewer(draft_id, chat, user)
                artifact = self.pipeline.get_draft(draft_id)
                if artifact.status == DraftStatus.REVIEW_READY:
                    artifact = self.pipeline.approve(draft_id, expected_hash)
                elif artifact.status not in {DraftStatus.APPROVED, DraftStatus.PUBLISHED} or artifact.content_sha256 != expected_hash:
                    raise ValueError("invalid approval state/hash")
                callback = "p:" + approval_callback(artifact)[2:]
                if reason := self.publication_block_reason():
                    await query.edit_message_text(f"검토 승인 기록: {artifact.id}\n상태: {artifact.status.value}\n발행 차단: {reason}")
                    return
                await query.edit_message_text(f"검토 승인 기록: {artifact.id}\n상태: {artifact.status.value}\n아래 버튼은 승인한 초안을 Git 발행하고 push 성공 후 Vault에 동기화합니다.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("승인한 초안 발행 / 동기화 재시도", callback_data=callback)]]))
            elif data.startswith(("p:", "c:")):
                draft_id, expected_hash = decode_callback("a:" + data[2:])
                self.pipeline.check_reviewer(draft_id, chat, user)
                if reason := self.publication_block_reason():
                    raise ValueError(reason)
                result = await asyncio.to_thread(self.publisher.publish, self.pipeline.store, draft_id, expected_hash,
                                                 sync=self.sync, reconcile=data.startswith("c:"))
                if result["success"]:
                    text = f"발행 상태: PUBLISHED\nCommit: {result['commit_sha']}\nVault: {result['sync_status']}"
                    if result.get("sync_error"):
                        text += "\n" + result["sync_error"]
                    if result.get("local_state_error"):
                        text += "\n로컬 상태 기록 실패: " + result["local_state_error"]
                else:
                    text = f"발행 차단: {result['status']}\n{result['error']}"
                prefix = "c:" if result["status"] == "PUSH_UNCERTAIN" else "p:"
                label = "원격 SHA로 발행 여부 확인" if prefix == "c:" else "발행 / 동기화 재시도"
                rows = [] if result.get("sync_status") == "SYNCED" else [[InlineKeyboardButton(label, callback_data=prefix + data[2:])]]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
            else:
                raise ValueError("invalid or obsolete callback; open a current review")
        except (ValueError, OSError, AttributeError) as error:
            await query.edit_message_text(f"처리 차단: {error}")

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
