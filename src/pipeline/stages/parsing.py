"""Stage 2: Document parsing — Map-Reduce chunked extraction from Markdown docs.

Map phase: split markdown by headings, LLM-extract requirements per chunk.
Reduce phase: deduplicate and merge overlapping requirements.

Every chunking operation is fully logged. Chunks are persisted to:
  storage/reports/{project_id}/chunks_{pipeline_id}.json
  storage/reports/{project_id}/chunks/chunk_{NNN}.txt
Any error during chunking fails the parsing stage immediately.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.models import Document
from src.llm.caller import llm_call
from src.llm.prompts.skill_loader import load_skill
from src.llm.types import LLMRequest
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.utils.file_storage import save
from src.utils.parse_cache import get as cache_get, set as cache_set
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)

TARGET_CHUNK_SIZE = 20_000
CHUNK_OVERLAP = 1_000
MAX_CHUNKS = 30
SINGLE_PASS_THRESHOLD = 40_000  # chars: docs <= this use single-shot LLM parse


class ParsingStage(AbstractStage):
    stage_name = "parsing"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return ["raw_texts"]

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["parsed_requirements"]

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        pid = stage_input.pipeline_id
        project_id = stage_input.project_id
        slog = get_stage_logger(pid, self.stage_name)
        slog.info(f"========== 文档解析阶段开始 ==========")
        slog.info(f"文档数量: {len(context.raw_texts)}")
        
        if not context.raw_texts:
            logger.error("parsing_no_raw_texts", pipeline_id=pid)
            return StageOutput(
                stage_name=self.stage_name,
                status="failed",
                error="导入阶段未产生任何可用文本",
            )

        doc_count = len(context.raw_texts)
        logger.info(
            "parsing_start",
            pipeline_id=pid,
            project_id=project_id,
            document_count=doc_count,
            doc_ids=[did[:8] for did in context.raw_texts.keys()],
            target_chunk_size=TARGET_CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            max_chunks=MAX_CHUNKS,
        )

        # Check cache first
        for doc_id, md_text in context.raw_texts.items():
            cached = await cache_get(md_text)
            if cached:
                # Remove internal cache metadata from stored output
                context.parsed_requirements = [cached]
                slog.info(f"缓存命中 (doc={doc_id[:8]}), 跳过解析")
                return StageOutput(
                    stage_name=self.stage_name,
                    status="completed",
                    data={"from_cache": True, "doc_id": doc_id[:8]},
                )

        # ── Phase 1: Parse — single-pass for small docs, Map-Reduce for large ──
        all_chunk_results: list[dict] = []
        chunk_files: list[str] = []
        chunk_metadata: dict = {}
        used_single_pass = False

        # Load skill prompt once
        skill = load_skill("requirement-parser")
        prompt_template = skill.body if skill else ""

        for doc_id, md_text in context.raw_texts.items():
            if len(md_text) <= SINGLE_PASS_THRESHOLD:
                # Single-pass: one LLM call = deterministic
                slog.info(f"单次解析模式 (doc={doc_id[:8]}, size={len(md_text)})")
                result = await self._single_pass_parse(md_text, pid, doc_id, prompt_template)
                if result:
                    all_chunk_results.append({
                        "doc_id": doc_id,
                        "requirements": [result],
                    })
                    used_single_pass = True
                    chunk_metadata = {"total_chunks": 1, "parse_success": 1, "parse_failed": 0}
                else:
                    # Fallback to Map-Reduce
                    slog.warning(f"单次解析失败，降级到Map-Reduce (doc={doc_id[:8]})")
                    break
            else:
                break  # any doc too large → use Map-Reduce for all

        if not all_chunk_results:
            # Map-Reduce fallback
            try:
                slog.info("Phase 1: Map — 开始文档分段处理...")
                all_chunk_results, chunk_files, chunk_metadata = await self._map_phase(
                    context, pid, project_id
                )
                slog.info(f"Map阶段完成: 总分段数={chunk_metadata['total_chunks']}, 成功={chunk_metadata['parse_success']}, 失败={chunk_metadata['parse_failed']}")
            except Exception as exc:
                slog.error(f"Map阶段异常: {exc}")
                logger.error("parsing_map_crash", pipeline_id=pid, error=str(exc))
                return StageOutput(
                    stage_name=self.stage_name,
                    status="failed",
                    error=f"文档分段过程异常: {str(exc)}",
                )

        if used_single_pass:
            slog.info(f"单次解析完成: {chunk_metadata.get('parse_success', 0)} docs parsed")
        parse_success = chunk_metadata.get("parse_success", 0)
        parse_failed = chunk_metadata.get("parse_failed", 0)
        total_chunks = chunk_metadata.get("total_chunks", 0)

        # ── Phase 2: Dedup — code-based first, LLM as fallback ──
        all_reqs, all_non_func, all_risks, all_test_points = self._collect_requirements(
            all_chunk_results
        )

        logger.info(
            "parsing_collected",
            pipeline_id=pid,
            functional_reqs=len(all_reqs),
            non_functional=len(all_non_func),
            risks=len(all_risks),
            test_points=len(all_test_points),
        )

        # Code-based dedup (deterministic)
        all_reqs = self._code_dedup(all_reqs)
        merged = {
            "functional_requirements": all_reqs,
            "non_functional_requirements": all_non_func,
            "risks": all_risks,
            "test_points": all_test_points,
        }

        # LLM dedup only when > 1 chunk AND code dedup might miss semantic merges
        if total_chunks > 1 and len(all_reqs) > 5:
            try:
                slog.info(f"Phase 2: LLM语义去重 (分段数={total_chunks})...")
                merged = await self._deduplicate_requirements(
                    pid, all_reqs, all_non_func, all_risks, all_test_points
                )
            except Exception as exc:
                slog.warning(f"LLM去重异常，使用代码去重结果: {exc}")
                logger.error("parsing_merge_crash", pipeline_id=pid, error=str(exc))

        
        # ── Phase 2.5: Determinize + Validate ──
        merged = self._determinize(merged)
        validation_issues = self._validate_output(merged)
        if validation_issues:
            slog.warning(f"校验发现问题: {validation_issues}")
            logger.warning("parsing_validation_issues", pipeline_id=pid, issues=validation_issues)
            merged["_validation_warnings"] = validation_issues
        else:
            slog.info("输出校验通过")
        slog.info(f"去重完成: 功能需求={len(merged.get('functional_requirements',[]))}, 非功能={len(merged.get('non_functional_requirements',[]))}")

        # Cache the result for future identical documents
        for doc_id, md_text in context.raw_texts.items():
            await cache_set(md_text, merged)
            break  # only cache first doc's result
        context.parsed_requirements = [merged]
        
        slog.info("========== 文档解析阶段完成 ==========")

        logger.info(
            "parsing_reduce_done",
            pipeline_id=pid,
            final_functional=len(merged.get("functional_requirements", [])),
            final_non_func=len(merged.get("non_functional_requirements", [])),
            final_risks=len(merged.get("risks", [])),
            final_test_points=len(merged.get("test_points", [])),
        )

        data = {
            "documents_parsed": len(all_chunk_results),
            "chunks_processed": total_chunks,
            "chunks_success": parse_success,
            "chunks_failed": parse_failed,
            "requirements_count": len(merged.get("functional_requirements", [])),
            "non_functional_count": len(merged.get("non_functional_requirements", [])),
            "risks_count": len(merged.get("risks", [])),
            "test_points_count": len(merged.get("test_points", [])),
            "chunk_files": chunk_files,
        }

        logger.info(
            "parsing_stage_done",
            pipeline_id=pid,
            total_chunks=total_chunks,
            success=parse_success,
            failed=parse_failed,
            final_requirements=data["requirements_count"],
        )

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data=data,
        )

    # ═══════════════════════════════════════════════════════════════
    # Map Phase
    # ═══════════════════════════════════════════════════════════════

    async def _map_phase(
        self, context, pid: str, project_id: str
    ) -> tuple[list[dict], list[str], dict]:
        """Run map phase: chunk each doc, LLM-extract per chunk, persist chunks."""
        all_chunk_results: list[dict] = []
        skill = load_skill("requirement-parser")
        prompt_template = skill.body
        total_chunks = 0
        parse_success = 0
        parse_failed = 0
        chunk_files: list[str] = []

        for doc_id, md_text in context.raw_texts.items():
            logger.info(
                "parsing_doc_chunking_begin",
                pipeline_id=pid,
                doc_id=doc_id,
                doc_id_short=doc_id[:8],
                text_length=len(md_text),
            )

            # ── Split ──
            try:
                chunks, split_trace = self._split_markdown_with_trace(md_text, pid, doc_id)
            except Exception as exc:
                logger.error(
                    "parsing_split_error",
                    pipeline_id=pid,
                    doc_id=doc_id[:8],
                    error=str(exc),
                )
                raise RuntimeError(f"文档 {doc_id[:8]} 分段失败: {str(exc)}") from exc

            logger.info(
                "parsing_chunks_created",
                pipeline_id=pid,
                doc_id=doc_id[:8],
                chunk_count=len(chunks),
                chunk_sizes=[len(c) for c in chunks],
                split_trace=split_trace,
            )

            # ── Persist each chunk to file ──
            saved_chunk_paths = await self._save_chunks(
                chunks, project_id, pid, doc_id
            )
            chunk_files.extend(saved_chunk_paths)

            # ── LLM-extract per chunk ──
            doc_requirements: list[dict] = []
            mslog = get_stage_logger(pid, self.stage_name)

            for ci, chunk in enumerate(chunks):
                total_chunks += 1
                if total_chunks > MAX_CHUNKS:
                    logger.warning(
                        "parsing_chunk_limit_hit",
                        pipeline_id=pid,
                        total_chunks=total_chunks,
                        max_chunks=MAX_CHUNKS,
                    )
                    chunk_result = {
                        "doc_id": doc_id,
                        "chunk_index": ci,
                        "functional_requirements": [],
                        "error": f"超出最大分段数限制 ({MAX_CHUNKS})",
                        "chunk_file": saved_chunk_paths[ci] if ci < len(saved_chunk_paths) else "",
                    }
                    doc_requirements.append(chunk_result)
                    parse_failed += 1
                    continue

                mslog.info(f"处理分段 [{ci+1}/{len(chunks)}] doc={doc_id[:8]}, 大小={len(chunk)}字符")
                logger.info(
                    "parsing_chunk_llm_begin",
                    pipeline_id=pid,
                    doc_id=doc_id[:8],
                    chunk_index=ci + 1,
                    chunk_total=len(chunks),
                    chunk_size=len(chunk),
                    chunk_preview=chunk[:200].replace("\n", "\\n"),
                )

                response = None
                last_error = None
                for retry in range(2):  # 1 initial + 1 retry
                    try:
                        response = await llm_call(
                            LLMRequest(
                                system_prompt=prompt_template,
                                user_prompt=chunk,
                                task_tag="parsing",
                                complexity="medium",
                                expect_json=True,
                                temperature=0.0,
                                pipeline_id=pid,
                                stage_name=self.stage_name,
                            )
                        )
                        break
                    except Exception as exc:
                        last_error = exc
                        if retry < 1:
                            logger.warning(
                                "parsing_chunk_retry",
                                pipeline_id=pid,
                                doc_id=doc_id[:8],
                                chunk_index=ci + 1,
                                attempt=retry + 1,
                                error=str(exc),
                            )
                        else:
                            logger.error(
                                "parsing_chunk_llm_crash",
                                pipeline_id=pid,
                                doc_id=doc_id[:8],
                                chunk_index=ci + 1,
                                error=str(exc),
                            )
                            raise RuntimeError(
                                f"文档 {doc_id[:8]} 第 {ci + 1}/{len(chunks)} 段 LLM 调用失败: {str(exc)}"
                            ) from exc

                chunk_file = saved_chunk_paths[ci] if ci < len(saved_chunk_paths) else ""

                if response.parsed_json and isinstance(response.parsed_json, dict):
                    reqs = response.parsed_json.get("functional_requirements", [])
                    non_func = response.parsed_json.get("non_functional_requirements", [])
                    mslog.info(f"分段 [{ci+1}/{len(chunks)}] 解析成功: 功能需求={len(reqs)}, 非功能={len(non_func)}")

                    chunk_result = {
                        "doc_id": doc_id,
                        "chunk_index": ci,
                        "chunk_content_head": chunk[:300],
                        "functional_requirements": reqs,
                        "non_functional_requirements": non_func,
                        "risks": response.parsed_json.get("risks", []),
                        "test_points": response.parsed_json.get("test_points", []),
                        "chunk_file": chunk_file,
                        "model": response.model,
                        "latency_ms": response.latency_ms,
                    }
                    doc_requirements.append(chunk_result)
                    parse_success += 1

                    logger.info(
                        "parsing_chunk_done",
                        pipeline_id=pid,
                        doc_id=doc_id[:8],
                        chunk_index=ci + 1,
                        requirements=len(reqs),
                        non_func=len(non_func),
                        model=response.model,
                        latency_ms=response.latency_ms,
                        chunk_file=chunk_file,
                    )
                else:
                    # Chunk LLM returned unparseable content — fail the entire stage immediately
                    raw_preview = (response.content or "")[:300]
                    error_detail = f"LLM 返回无法解析: model={response.model}"
                    logger.error(
                        "parsing_chunk_failed",
                        pipeline_id=pid,
                        doc_id=doc_id[:8],
                        chunk_index=ci + 1,
                        model=response.model,
                        raw_head=raw_preview,
                        chunk_file=chunk_file,
                    )
                    raise RuntimeError(
                        f"文档 {doc_id[:8]} 第 {ci + 1} 段解析失败: "
                        f"{error_detail}。原始返回: {raw_preview}"
                    )

            all_chunk_results.append({
                "doc_id": doc_id,
                "requirements": doc_requirements,
            })

            # Update document status in DB
            result = await self._db.execute(
                select(Document).where(Document.id == doc_id)
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.parsed_content = doc_requirements
                doc.status = "parsed" if doc_requirements else "failed"
                self._db.add(doc)

        await self._db.commit()

        # ── Persist aggregate chunk results JSON ──
        aggregate_path = await self._save_chunk_aggregate(
            all_chunk_results, project_id, pid
        )
        chunk_files.insert(0, aggregate_path)

        logger.info(
            "parsing_map_done",
            pipeline_id=pid,
            total_chunks=total_chunks,
            success=parse_success,
            failed=parse_failed,
            aggregate_file=aggregate_path,
        )

        metadata = {
            "total_chunks": total_chunks,
            "parse_success": parse_success,
            "parse_failed": parse_failed,
        }
        return all_chunk_results, chunk_files, metadata

    # ═══════════════════════════════════════════════════════════════
    # Chunk persistence
    # ═══════════════════════════════════════════════════════════════

    async def _save_chunks(
        self, chunks: list[str], project_id: str, pid: str, doc_id: str
    ) -> list[str]:
        """Save each chunk to a file. Returns list of relative paths."""
        paths = []
        pid_short = pid[:8]
        doc_short = doc_id[:8]
        chunk_dir = f"reports/{project_id}/chunks"
        for i, chunk in enumerate(chunks):
            filename = f"chunk_{pid_short}_{doc_short}_{i+1:03d}.txt"
            rel_path = f"{chunk_dir}/{filename}"
            try:
                await save(rel_path, chunk.encode("utf-8"))
                paths.append(rel_path)
                logger.info(
                    "chunk_saved",
                    pipeline_id=pid,
                    doc_id=doc_short,
                    chunk_index=i + 1,
                    path=rel_path,
                    size=len(chunk),
                )
            except Exception as exc:
                logger.error(
                    "chunk_save_failed",
                    pipeline_id=pid,
                    chunk_index=i + 1,
                    path=rel_path,
                    error=str(exc),
                )
                raise RuntimeError(f"分段文件保存失败 ({rel_path}): {str(exc)}") from exc
        return paths

    async def _save_chunk_aggregate(
        self, all_chunk_results: list[dict], project_id: str, pid: str
    ) -> str:
        """Save full chunk analysis results as a JSON file."""
        pid_short = pid[:8]
        rel_path = f"reports/{project_id}/chunks_{pid_short}.json"
        aggregate = {
            "pipeline_id": pid,
            "project_id": project_id,
            "chunk_results": all_chunk_results,
        }
        try:
            content = json.dumps(aggregate, ensure_ascii=False, indent=2)
            await save(rel_path, content.encode("utf-8"))
            logger.info(
                "chunk_aggregate_saved",
                pipeline_id=pid,
                path=rel_path,
                size=len(content),
                doc_count=len(all_chunk_results),
            )
            return rel_path
        except Exception as exc:
            logger.error(
                "chunk_aggregate_save_failed",
                pipeline_id=pid,
                error=str(exc),
            )
            raise RuntimeError(f"分段汇总文件保存失败: {str(exc)}") from exc

    # ═══════════════════════════════════════════════════════════════
    # Markdown splitting with full trace
    # ═══════════════════════════════════════════════════════════════


    # ═══════════════════════════════════════════════════════════════
    # Single-Pass Parsing — one LLM call for small/medium docs
    # ═══════════════════════════════════════════════════════════════

    async def _single_pass_parse(
        self, md_text: str, pid: str, doc_id: str, prompt: str
    ) -> dict:
        """Parse entire document in one LLM call. Eliminates chunk randomness."""
        logger.info("parsing_single_pass", pipeline_id=pid, doc_id=doc_id[:8], text_len=len(md_text))
        try:
            response = await llm_call(
                LLMRequest(
                    system_prompt=prompt,
                    user_prompt=md_text[:SINGLE_PASS_THRESHOLD],
                    task_tag="parsing",
                    complexity="medium",
                    expect_json=True,
                    temperature=0.0,
                    max_tokens=16384,
                    pipeline_id=pid,
                    stage_name=self.stage_name,
                )
            )
            if response.parsed_json and isinstance(response.parsed_json, dict):
                return response.parsed_json
            logger.warning("parsing_single_pass_no_json", pipeline_id=pid, doc_id=doc_id[:8])
            return {}
        except Exception as exc:
            logger.error("parsing_single_pass_failed", pipeline_id=pid, doc_id=doc_id[:8], error=str(exc))
            return {}

    # ═══════════════════════════════════════════════════════════════
    # Code-Based Dedup — deterministic, no LLM randomness
    # ═══════════════════════════════════════════════════════════════

    def _code_dedup(self, reqs: list[dict]) -> list[dict]:
        """Deduplicate by ID first, then by description similarity > 85%."""
        seen: list[dict] = []
        sorted_reqs = sorted(reqs, key=lambda r: r.get("id", ""))
        for req in sorted_reqs:
            is_dup = False
            rid = req.get("id", "")
            rdesc = req.get("description", "")
            for s in seen:
                if rid and rid == s.get("id"):
                    is_dup = True
                    break
                if rdesc and self._text_similarity(rdesc, s.get("description", "")) > 0.85:
                    is_dup = True
                    break
            if not is_dup:
                seen.append(req)
        logger.info("parsing_code_dedup", before=len(sorted_reqs), after=len(seen))
        return seen

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Simple Jaccard similarity on character trigrams. 1.0 = identical."""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        def _trigrams(s):
            return set(s[i:i+3] for i in range(len(s) - 2))
        ta = _trigrams(a)
        tb = _trigrams(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    # ═══════════════════════════════════════════════════════════════
    # Deterministic Post-Processing — fix IDs and sort
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _determinize(parsed: dict) -> dict:
        """Force deterministic ordering: re-number IDs, sort arrays."""
        # Re-number functional requirements
        func_reqs = parsed.get("functional_requirements", [])
        for i, req in enumerate(func_reqs, 1):
            if isinstance(req, dict):
                req["id"] = f"FR-{i:03d}"
        # Sort by id
        func_reqs.sort(key=lambda r: r.get("id", ""))
        parsed["functional_requirements"] = func_reqs

        # Re-number non-functional requirements
        nf_reqs = parsed.get("non_functional_requirements", [])
        for i, req in enumerate(nf_reqs, 1):
            if isinstance(req, dict):
                req["id"] = f"NFR-{i:03d}"
        nf_reqs.sort(key=lambda r: r.get("id", ""))
        parsed["non_functional_requirements"] = nf_reqs

        # Sort constraints and data_entities
        if isinstance(parsed.get("constraints"), list):
            parsed["constraints"] = sorted(parsed["constraints"])
        if isinstance(parsed.get("data_entities"), list):
            parsed["data_entities"] = sorted(
                parsed["data_entities"], key=lambda e: e.get("name", "")
            )

        return parsed

    # ═══════════════════════════════════════════════════════════════
    # Output Validation — schema enforcement + quality gates
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _validate_output(data: dict) -> dict:
        """Validate parsed output. Returns issues dict (empty = pass)."""
        issues = {}

        # Schema: must have functional_requirements
        if "functional_requirements" not in data:
            issues["missing_section"] = "缺少 functional_requirements 字段"
            return issues

        reqs = data.get("functional_requirements", [])
        if not isinstance(reqs, list):
            issues["invalid_type"] = "functional_requirements 不是数组"
            return issues

        # ID continuity
        ids = [r.get("id", "") for r in reqs if isinstance(r, dict)]
        expected = [f"FR-{i:03d}" for i in range(1, len(ids) + 1)]
        if ids != expected:
            issues["id_sequence"] = f"MISMATCH: expected {expected[:5]}..., got {ids[:5]}..."

        # Required fields per requirement
        required_fields = ["description", "priority", "test_type"]
        missing_fields = []
        vague_descriptions = []
        for r in reqs:
            if not isinstance(r, dict):
                continue
            rid = r.get("id", "?")
            for field in required_fields:
                if not r.get(field):
                    missing_fields.append(f"{rid}.{field}")
            desc = r.get("description", "")
            if isinstance(desc, str) and len(desc) < 10:
                vague_descriptions.append(rid)

        if missing_fields:
            issues["missing_fields"] = missing_fields
        if vague_descriptions:
            issues["vague_descriptions"] = vague_descriptions

        return issues

    def _split_markdown_with_trace(
        self, md_text: str, pid: str, doc_id: str
    ) -> tuple[list[str], list[dict]]:
        """Split markdown into semantic chunks, logging every step.

        Returns (chunks, trace_log) where trace_log records each
        decision (headings found, merge/split actions, overlap added).
        """
        trace: list[dict] = []
        doc_short = doc_id[:8]

        def _trace(action: str, **kwargs):
            entry = {"action": action, **kwargs}
            trace.append(entry)
            logger.info(
                f"parsing_split_{action}",
                pipeline_id=pid,
                doc_id=doc_short,
                **kwargs,
            )

        text_len = len(md_text)
        _trace("split_begin", text_length=text_len, target_size=TARGET_CHUNK_SIZE)

        # Small doc — no splitting needed
        if text_len <= TARGET_CHUNK_SIZE:
            _trace("split_skip", reason="文档未超过阈值，无需切分", text_length=text_len)
            return [md_text], trace

        # Detect headings
        heading_pattern = re.compile(
            r'^(#{1,4}\s+.+|第[一二三四五六七八九十百千\d]+[章节部分篇]|'
            r'[（(][一二三四五六七八九十\d]+[)）]|'
            r'\d+[.、]\s*.+)$',
            re.MULTILINE,
        )
        headings = [(m.start(), m.group().strip()) for m in heading_pattern.finditer(md_text)]

        _trace(
            "headings_detected",
            heading_count=len(headings),
            headings=[h[1][:60] for h in headings[:20]],
            positions=[h[0] for h in headings[:20]],
        )

        if not headings:
            _trace("no_headings", action_taken="按段落切分")
            chunks = self._split_by_paragraphs_with_trace(md_text, pid, doc_short, trace)
            chunks = self._add_overlap(chunks, pid, doc_short, trace)
            _trace("split_done", final_chunk_count=len(chunks), strategy="paragraph")
            return chunks[:MAX_CHUNKS], trace

        # Split at heading positions
        chunks = []
        for i, (pos, heading) in enumerate(headings):
            next_pos = headings[i + 1][0] if i + 1 < len(headings) else text_len
            segment = md_text[pos:next_pos].strip()
            if segment:
                chunks.append(segment)

        _trace(
            "heading_split",
            initial_chunk_count=len(chunks),
            chunk_sizes=[len(c) for c in chunks],
        )

        # Merge small adjacent chunks
        chunks_before_merge = len(chunks)
        chunks = self._merge_small_chunks_with_trace(chunks, pid, doc_short, trace)
        _trace(
            "merge_small",
            before=chunks_before_merge,
            after=len(chunks),
            merged_count=chunks_before_merge - len(chunks),
        )

        # Split oversized chunks
        chunks = self._split_oversized_with_trace(chunks, pid, doc_short, trace)

        # Add overlap
        chunks = self._add_overlap(chunks, pid, doc_short, trace)

        _trace(
            "split_done",
            final_chunk_count=len(chunks),
            final_sizes=[len(c) for c in chunks],
            strategy="heading",
        )

        return chunks[:MAX_CHUNKS], trace

    # ── Paragraph splitting ──

    def _split_by_paragraphs_with_trace(
        self, text: str, pid: str, doc_short: str, trace: list[dict]
    ) -> list[str]:
        paragraphs = re.split(r'\n\n+', text)
        chunks = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) > TARGET_CHUNK_SIZE and current:
                chunks.append(current.strip())
                current = para
            else:
                current += "\n\n" + para if current else para

        if current.strip():
            chunks.append(current.strip())

        return chunks

    # ── Merge small chunks ──

    def _merge_small_chunks_with_trace(
        self, chunks: list[str], pid: str, doc_short: str, trace: list[dict]
    ) -> list[str]:
        merged = []
        buffer = ""
        for i, chunk in enumerate(chunks):
            if len(buffer) + len(chunk) < TARGET_CHUNK_SIZE * 0.7:
                buffer += "\n\n" + chunk if buffer else chunk
            else:
                if buffer:
                    merged.append(buffer.strip())
                buffer = chunk
        if buffer:
            merged.append(buffer.strip())
        return merged if merged else chunks

    # ── Split oversized ──

    def _split_oversized_with_trace(
        self, chunks: list[str], pid: str, doc_short: str, trace: list[dict]
    ) -> list[str]:
        result = []
        for chunk in chunks:
            if len(chunk) <= TARGET_CHUNK_SIZE * 1.5:
                result.append(chunk)
            else:
                # Recursive paragraph split
                sub = self._split_by_paragraphs_with_trace(chunk, pid, doc_short, [])
                result.extend(sub)
        return result

    # ── Overlap ──

    def _add_overlap(
        self, chunks: list[str], pid: str, doc_short: str, trace: list[dict]
    ) -> list[str]:
        if len(chunks) <= 1:
            return chunks

        overlapped = []
        for i, chunk in enumerate(chunks):
            prefix = ""
            suffix = ""
            if i > 0:
                tail = chunks[i - 1][-CHUNK_OVERLAP:]
                prefix = f"<!-- 上文延续，{len(tail)} 字符重叠 -->\n{tail}\n---\n"
            if i < len(chunks) - 1:
                head = chunks[i + 1][:CHUNK_OVERLAP]
                suffix = f"\n---\n<!-- 下文预览，{len(head)} 字符重叠 -->\n{head}"

            overlapped.append(prefix + chunk + suffix)

        return overlapped

    # ═══════════════════════════════════════════════════════════════
    # Reduce phase
    # ═══════════════════════════════════════════════════════════════

    def _collect_requirements(self, all_chunk_results: list[dict]) -> tuple:
        all_reqs = []
        all_non_func = []
        all_risks = []
        all_test_points = []

        for doc_result in all_chunk_results:
            for chunk_req in doc_result.get("requirements", []):
                for req in chunk_req.get("functional_requirements", []):
                    if isinstance(req, dict):
                        req["_doc_id"] = doc_result["doc_id"]
                        req["_chunk_index"] = chunk_req.get("chunk_index", -1)
                        all_reqs.append(req)
                for nf in chunk_req.get("non_functional_requirements", []):
                    if isinstance(nf, dict):
                        nf["_doc_id"] = doc_result["doc_id"]
                        all_non_func.append(nf)
                for risk in chunk_req.get("risks", []):
                    if isinstance(risk, dict):
                        risk["_doc_id"] = doc_result["doc_id"]
                        all_risks.append(risk)
                for tp in chunk_req.get("test_points", []):
                    if isinstance(tp, dict):
                        tp["_doc_id"] = doc_result["doc_id"]
                        all_test_points.append(tp)

        return all_reqs, all_non_func, all_risks, all_test_points

    async def _deduplicate_requirements(
        self, pipeline_id: str, all_reqs: list, all_non_func: list,
        all_risks: list, all_test_points: list,
    ) -> dict:
        if not all_reqs and not all_non_func and not all_risks and not all_test_points:
            return {
                "functional_requirements": all_reqs,
                "non_functional_requirements": all_non_func,
                "risks": all_risks,
                "test_points": all_test_points,
            }

        merge_input = json.dumps({
            "functional_requirements": all_reqs,
            "non_functional_requirements": all_non_func,
            "risks": all_risks,
            "test_points": all_test_points,
        }, ensure_ascii=False, indent=2)

        logger.info(
            "parsing_merge_start",
            pipeline_id=pipeline_id,
            input_length=len(merge_input),
            func_count=len(all_reqs),
        )

        response = await llm_call(
            LLMRequest(
                system_prompt=(
                    "你是一个需求分析专家。请将以下来自多个文档片段的需求进行去重和合并。"
                    "规则：1. 相同或高度相似的需求合并为一条；2. 保留更详细版本；"
                    "3. 按优先级排序；4. 保留 _doc_id 和 _chunk_index 元数据；"
                    "5. 输出 JSON 格式。"
                ),
                user_prompt=(
                    f"请对以下需求进行去重合并，保持相同 JSON 结构输出：\n\n"
                    f"```json\n{merge_input[:50000]}\n```"
                ),
                task_tag="parsing",
                complexity="medium",
                expect_json=True,
                temperature=0.0,
                max_tokens=16384,
                pipeline_id=pipeline_id,
                stage_name="parsing",
            )
        )

        if response.parsed_json and isinstance(response.parsed_json, dict):
            logger.info(
                "parsing_merge_done",
                pipeline_id=pipeline_id,
                original_func=len(all_reqs),
                merged_func=len(response.parsed_json.get("functional_requirements", [])),
            )
            return response.parsed_json
        else:
            logger.warning(
                "parsing_merge_failed_using_raw",
                pipeline_id=pipeline_id,
                model=response.model,
            )
            return {
                "functional_requirements": all_reqs,
                "non_functional_requirements": all_non_func,
                "risks": all_risks,
                "test_points": all_test_points,
            }
