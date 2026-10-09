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
from src.workflow.service import WorkflowService, ProductionAdapters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TrendBotApp:
    """Telegram Bot application managing daily briefings, user selection, and blog publishing."""

    def __init__(self, *, pipeline: EditorialPipeline | None = None, publisher=None, sync=None, workflow=None):
        self.profiler = InterestProfiler()
        self.collector = TrendOrchestrator()
        self.matcher = TrendMatcher()
        self.pipeline = pipeline or EditorialPipeline()
        self.publisher = publisher or GitPublisher()
        self.sync = sync or ObsidianSync()
        self.workflow = workflow or WorkflowService(self.pipeline.store.root / 'workflow', writer=self.pipeline.writer,
            adapters=ProductionAdapters(self.profiler, self.collector, self.matcher), publisher=self.publisher, sync=self.sync)
        self.scheduler = AsyncIOScheduler()

        # Cache of current curated topics
        self.current_topics: Dict[int, CuratedTopic] = {}

    def publication_block_reason(self):
        from src.workflow.publication import production_block_reason
        return production_block_reason()

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

    async def trigger_briefing(self, chat_id: Optional[str] = None, context: Optional[ContextTypes.DEFAULT_TYPE] = None,
                               *, run_id=None, mode='shadow', days=14, topic_count=5):
        """Create a typed configured briefing or resume the same frozen delivery."""
        from src.workflow.telegram import briefing_message_kwargs
        chat, user = self.reviewer_identity()
        if str(chat_id or settings.telegram_chat_id) != chat:
            raise ValueError('unauthorized review chat')
        bot = context.bot if context else None
        sender = None
        if bot is not None:
            async def sender(payload):
                await bot.send_message(**briefing_message_kwargs(payload))
        if run_id is None:
            run = await self.workflow.create_briefing(mode=mode, days=days, topic_count=topic_count, sender=sender)
        else:
            run = await self.workflow.resume(run_id, briefing_sender=sender)
        curated = [CuratedTopic.model_validate(t) for t in run.checkpoints.get('curation', [])]
        self.current_topics = {t.rank: t for t in curated}
        return run

    async def now_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat, user = self.reviewer_identity()
        if str(update.effective_chat.id) != chat or str(update.effective_user.id) != user:
            raise ValueError('unauthorized reviewer')
        await update.message.reply_text('최근 관심 기록과 트렌드를 수집합니다.')
        run = await self.trigger_briefing(chat_id=chat, context=context)
        await update.message.reply_text(f'Run: {run.id}\nState: {run.status}')

    def reviewer_identity(self):
        from src.workflow.telegram import configured_recipient
        return configured_recipient()

    async def send_review(self, bot, chat_id, artifact):
        chat, user = self.reviewer_identity()
        if str(chat_id) != chat:
            raise ValueError('unauthorized review chat')
        identity = f'telegram:{chat}:{user}'
        async def sender(current):
            for path in (current.content_path, current.content_path.parent / 'review.md', *current.media_paths):
                if path == current.content_path and path.stat().st_size == 0:
                    continue
                with path.open('rb') as document:
                    await bot.send_document(chat_id=chat, document=document, filename=path.name)
        try:
            run = self.workflow.find_run(artifact.id)
        except ValueError:
            # A historic local bundle requires explicit complete delivery first.
            await self.workflow.review_legacy(self.pipeline.store, artifact.id, artifact.content_sha256, reviewer=identity, sender=sender)
            run = self.workflow.find_run(artifact.id)
        else:
            await self.workflow.deliver(run.id, reviewer=identity, sender=sender)
        packet = json.loads(artifact.evidence_path.read_text(encoding='utf-8')) or {}
        rows = [[InlineKeyboardButton(source['title'][:80], url=source['url'])]
                for source in packet.get('sources', []) if source['url'].startswith(('http://', 'https://'))]
        if artifact.status == DraftStatus.REVIEW_READY:
            rows.append([InlineKeyboardButton('전체 검토 후 승인', callback_data=approval_callback(artifact))])
        rows.append([InlineKeyboardButton('새 수정본 생성', callback_data='r:' + approval_callback(artifact)[2:])])
        await bot.send_message(chat_id=chat, text=f'초안 상태: {artifact.status.value}\n전체 초안: {artifact.content_path}\nRun: {run.id}\nSHA-256: {artifact.content_sha256}\n전체 본문과 근거 보고서를 첨부했습니다. 현재 run 모드: {run.mode}', reply_markup=InlineKeyboardMarkup(rows))

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        try:
            chat, user = self.reviewer_identity()
            if str(query.message.chat_id) != chat or str(getattr(query.from_user, 'id', '')) != user:
                raise ValueError('unauthorized reviewer')
        except (ValueError, AttributeError) as error:
            await query.answer(f'처리 차단: {error}', show_alert=True)
            return
        await query.answer()
        identity = f'telegram:{chat}:{user}'
        try:
            data = query.data or ''
            if data == 'refresh_topics':
                await self.trigger_briefing(chat_id=chat, context=context)
                return
            if data.startswith('w:'):
                _, run_id, rank = data.split(':')
                run = await self.workflow.select(run_id, int(rank), reviewer=identity)
                if run.draft_id:
                    await self.send_review(context.bot, chat, self.workflow.pipeline(run).get_draft(run.draft_id))
                else:
                    await query.edit_message_text(f'{run.status}: {run.id}; resume after research recovery.')
                return
            if data.startswith(('s:', 'select_')):
                raise ValueError('obsolete topic selection; refresh the briefing to bind a durable run')
            prefix = data[:2]
            draft_id, expected_hash = decode_callback('a:' + data[2:])
            run = self.workflow.find_run(draft_id)
            self.workflow._identity(run, identity)
            if prefix == 'r:':
                run = await self.workflow.revise(run.id, draft_id, expected_hash, reviewer=identity)
                await self.send_review(context.bot, chat, self.workflow.pipeline(run).get_draft(run.draft_id))
                return
            if prefix == 'a:':
                run = await asyncio.to_thread(self.workflow.approve, run.id, draft_id, expected_hash, reviewer=identity)
                rows = []
                if run.mode == 'shadow':
                    rows.append([InlineKeyboardButton('이 승인본 한 편을 reviewed trial로 발행 허용', callback_data='t:' + data[2:])])
                else:
                    rows.append([InlineKeyboardButton('승인본 발행 / 동기화 재시도', callback_data='p:' + data[2:])])
                await query.edit_message_text(f'검토 승인 기록: {draft_id}\nMode: {run.mode}', reply_markup=InlineKeyboardMarkup(rows))
                return
            if prefix == 't:':
                await asyncio.to_thread(self.workflow.trial, run.id, draft_id, expected_hash, reviewer=identity)
                await query.edit_message_text('이 승인본 한 편의 reviewed trial을 기록했습니다. 발행은 다음 버튼으로 실행합니다.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('승인본 발행', callback_data='p:' + data[2:])]]))
                return
            if prefix not in ('p:', 'c:'):
                raise ValueError('invalid callback')
            result = await self.workflow.publish(run.id, draft_id, expected_hash, reviewer=identity, reconcile=prefix == 'c:')
            retry = 'c:' if result['status'] == 'PUSH_UNCERTAIN' else 'p:'
            rows = [] if result.get('sync_status') == 'SYNCED' and not result.get('local_state_error') else [[InlineKeyboardButton('원격 확인 / 재시도', callback_data=retry + data[2:])]]
            await query.edit_message_text(json.dumps(result, ensure_ascii=False), reply_markup=InlineKeyboardMarkup(rows))
        except (ValueError, OSError, AttributeError, KeyError, TypeError) as error:
            await query.edit_message_text(f'처리 차단: {error}')

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
