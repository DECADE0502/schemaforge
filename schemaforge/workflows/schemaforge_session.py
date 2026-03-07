"""SchemaForge 统一设计工作台。"""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
from pathlib import Path

from schemaforge.design.synthesis import (
    DesignBundle,
    DesignRecipeSynthesizer,
    ExactPartResolver,
    UserDesignRequest,
    apply_request_updates,
    parse_design_request,
    parse_revision_request,
)
from schemaforge.ingest.datasheet_extractor import (
    apply_user_answers,
    extract_from_image,
    extract_from_pdf,
)
from schemaforge.library.service import LibraryService
from schemaforge.library.store import ComponentStore
from schemaforge.library.symbol_builder import build_symbol
from schemaforge.library.validator import DeviceDraft, PinDraft
from schemaforge.schematic.renderer import TopologyRenderer
from schemaforge.workflows.design_session import DesignSession


@dataclass(slots=True)
class ImportPreview:
    """待确认导入预览。"""

    draft: DeviceDraft
    symbol_preview_base64: str
    questions: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "part_number": self.draft.part_number,
            "category": self.draft.category,
            "package": self.draft.package,
            "questions": list(self.questions),
            "symbol_preview_base64": self.symbol_preview_base64,
        }


@dataclass(slots=True)
class SchemaForgeTurnResult:
    """会话层返回结果。"""

    status: str
    message: str
    request: UserDesignRequest | None = None
    bundle: DesignBundle | None = None
    import_preview: ImportPreview | None = None
    missing_part_number: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "request": (
                {
                    "raw_text": self.request.raw_text,
                    "part_number": self.request.part_number,
                    "category": self.request.category,
                    "v_in": self.request.v_in,
                    "v_out": self.request.v_out,
                    "i_out": self.request.i_out,
                    "wants_led": self.request.wants_led,
                    "led_color": self.request.led_color,
                    "led_current_ma": self.request.led_current_ma,
                }
                if self.request
                else None
            ),
            "bundle": self.bundle.to_dict() if self.bundle else None,
            "import_preview": (
                self.import_preview.to_dict() if self.import_preview else None
            ),
            "missing_part_number": self.missing_part_number,
            "warnings": list(self.warnings),
        }


class SchemaForgeSession:
    """统一设计会话。"""

    def __init__(self, store_dir: Path | str, use_mock: bool = True) -> None:
        self.store_dir = Path(store_dir)
        self.use_mock = use_mock
        self._store = ComponentStore(self.store_dir)
        self._service = LibraryService(self.store_dir)
        self._resolver = ExactPartResolver(self._store)
        self._synthesizer = DesignRecipeSynthesizer()
        self._request: UserDesignRequest | None = None
        self._device = None
        self._bundle: DesignBundle | None = None
        self._parameter_overrides: dict[str, str] = {}
        self._pending_draft: DeviceDraft | None = None
        self._pending_app_circuit: dict[str, object] = {}  # P3: 待附加的应用电路

    @property
    def bundle(self) -> DesignBundle | None:
        return self._bundle

    def start(self, user_input: str) -> SchemaForgeTurnResult:
        request = parse_design_request(user_input)
        self._request = request
        self._parameter_overrides = {}

        if request.part_number:
            device = self._resolver.resolve(request.part_number)
            if device is None:
                return SchemaForgeTurnResult(
                    status="needs_asset",
                    message=(
                        f"本地器件库里没有精确型号 {request.part_number}，"
                        "请上传 datasheet PDF 或引脚图片继续导入。"
                    ),
                    request=request,
                    missing_part_number=request.part_number,
                )
            return self._build_from_device(device, message=f"已精确命中 {device.part_number}。")

        legacy = DesignSession(self.store_dir, use_mock=self.use_mock).run(user_input)
        matched = next((item for item in legacy.modules if item.device), None)
        if matched and matched.device:
            return self._build_from_device(
                matched.device,
                message="已按现有主链识别器件并生成设计。",
            )
        return SchemaForgeTurnResult(
            status="error",
            message=legacy.error or "当前请求无法自动完成，请明确器件型号或上传资料。",
            request=request,
        )

    def ingest_asset(self, filepath: str) -> SchemaForgeTurnResult:
        if self._request is None:
            return SchemaForgeTurnResult(
                status="error",
                message="请先发起设计请求，再上传资料。",
            )

        path = Path(filepath)
        hint = self._request.part_number
        if path.suffix.lower() == ".pdf":
            result = extract_from_pdf(str(path), hint=hint, use_mock=self.use_mock)
        else:
            result = extract_from_image(str(path), hint=hint, use_mock=self.use_mock)

        if not result.success or result.draft is None:
            return SchemaForgeTurnResult(
                status="error",
                message=result.error_message or "资料解析失败。",
                request=self._request,
            )

        draft = result.draft
        if hint and not draft.part_number:
            draft = apply_user_answers(draft, {"part_number": hint})

        preview_base64 = ""
        if draft.pins:
            symbol = build_symbol(
                part_number=draft.part_number or hint or "UNKNOWN",
                pins_data=[
                    {
                        "name": pin.name,
                        "number": pin.number,
                        "type": pin.pin_type,
                        "description": pin.description,
                    }
                    for pin in draft.pins
                ],
                category=draft.category,
                package=draft.package,
            )
            preview_base64 = base64.b64encode(
                TopologyRenderer.render_symbol_preview(symbol, label=draft.part_number)
            ).decode("ascii")

        self._pending_draft = draft
        self._pending_app_circuit = result.application_circuit
        preview = ImportPreview(
            draft=draft,
            symbol_preview_base64=preview_base64,
            questions=result.questions,
        )
        return SchemaForgeTurnResult(
            status="needs_confirmation",
            message="资料已解析，请确认导入信息后继续设计。",
            request=self._request,
            import_preview=preview,
        )

    def confirm_import(
        self,
        answers: dict[str, object] | None = None,
    ) -> SchemaForgeTurnResult:
        if self._pending_draft is None or self._request is None:
            return SchemaForgeTurnResult(
                status="error",
                message="当前没有待确认的导入任务。",
                request=self._request,
            )

        draft = self._apply_draft_answers(self._pending_draft, answers or {})

        # --- 安全落库流程: 校验 → 生成 symbol → 试转换(不写盘) → 写盘 ---

        # 1. 校验草稿（不跳过）
        validation = self._service.validate_only(draft)
        if not validation.is_valid:
            return SchemaForgeTurnResult(
                status="needs_confirmation",
                message="草稿校验未通过，请补充信息: "
                + "; ".join(e.message for e in validation.errors),
                request=self._request,
                import_preview=ImportPreview(
                    draft=draft,
                    symbol_preview_base64="",
                    questions=[
                        {"field": e.field_path, "message": e.message, "suggestion": e.suggestion}
                        for e in validation.errors
                    ],
                ),
            )

        # 2. 生成 symbol（在落库前）
        symbol = None
        if draft.pins:
            symbol = build_symbol(
                part_number=draft.part_number,
                pins_data=[
                    {
                        "name": pin.name,
                        "number": pin.number,
                        "type": pin.pin_type,
                        "description": pin.description,
                    }
                    for pin in draft.pins
                ],
                category=draft.category,
                package=draft.package,
            )

        # 3. 试转换（不写盘），确保 DeviceModel 构造成功
        dry_run = self._service.add_device_from_draft(
            draft,
            force=True,
            skip_dedupe=True,
            persist=False,
        )
        if not dry_run.success or dry_run.device is None:
            return SchemaForgeTurnResult(
                status="error",
                message=dry_run.error_message or "器件模型转换失败，无法入库。",
                request=self._request,
            )

        # 4. 一切通过，写盘
        add_result = self._service.add_device_from_draft(
            draft,
            force=True,
            skip_dedupe=True,
        )
        if not add_result.success or add_result.device is None:
            return SchemaForgeTurnResult(
                status="error",
                message=add_result.error_message or "器件入库失败。",
                request=self._request,
            )

        # 5. 附加 symbol
        if symbol is not None:
            self._service.update_device_symbol(draft.part_number, symbol)

        # 6. 附加 datasheet 提取的应用电路 recipe
        if self._pending_app_circuit:
            from schemaforge.ingest.datasheet_extractor import (
                build_recipe_from_application_circuit,
            )

            recipe = build_recipe_from_application_circuit(
                self._pending_app_circuit,
                part_number=draft.part_number,
            )
            if recipe is not None:
                self._service.update_device_recipe(draft.part_number, recipe)

        device = self._service.get(draft.part_number)
        if device is None:
            return SchemaForgeTurnResult(
                status="error",
                message="导入成功但重新加载器件失败。",
                request=self._request,
            )

        self._pending_draft = None
        self._pending_app_circuit = {}
        return self._build_from_device(device, message=f"已导入并应用 {device.part_number}。")

    def revise(self, user_input: str) -> SchemaForgeTurnResult:
        if self._request is None or self._device is None:
            return SchemaForgeTurnResult(
                status="error",
                message="当前还没有可修改的设计。",
            )

        param_updates, request_updates = parse_revision_request(user_input)
        if not param_updates and not request_updates:
            return SchemaForgeTurnResult(
                status="error",
                message="暂时无法理解这条修改请求，请再具体一点。",
                request=self._request,
            )

        if request_updates:
            self._request = apply_request_updates(self._request, **request_updates)
        self._parameter_overrides.update(param_updates)
        return self._build_from_device(
            self._device,
            message="已在现有设计上完成局部修改。",
        )

    def _build_from_device(self, device, *, message: str) -> SchemaForgeTurnResult:
        assert self._request is not None
        bundle = self._synthesizer.build_bundle(
            device,
            self._request,
            parameter_overrides=self._parameter_overrides,
        )
        self._device = bundle.device
        self._bundle = bundle
        self._store.save_device(bundle.device)
        return SchemaForgeTurnResult(
            status="generated",
            message=message,
            request=self._request,
            bundle=bundle,
        )

    def _apply_draft_answers(
        self,
        draft: DeviceDraft,
        answers: dict[str, object],
    ) -> DeviceDraft:
        simple_answers: dict[str, str] = {}
        for key in [
            "part_number",
            "manufacturer",
            "description",
            "category",
            "package",
            "datasheet_url",
        ]:
            if key in answers and isinstance(answers[key], str):
                simple_answers[key] = answers[key]

        updated = apply_user_answers(draft, simple_answers)
        updates: dict[str, object] = {}

        if "pins" in answers and isinstance(answers["pins"], list):
            updates["pins"] = [
                PinDraft(
                    name=str(item.get("name", "")),
                    number=str(item.get("number", "")),
                    pin_type=str(item.get("type", item.get("pin_type", "passive"))),
                    description=str(item.get("description", "")),
                )
                for item in answers["pins"]
                if isinstance(item, dict)
            ]
            updates["pin_count"] = len(updates["pins"])

        if "specs" in answers and isinstance(answers["specs"], dict):
            updates["specs"] = {
                str(key): str(value) for key, value in answers["specs"].items()
            }

        if "aliases" in answers and isinstance(answers["aliases"], list):
            updates["aliases"] = [str(item) for item in answers["aliases"]]

        if updates:
            updated = updated.model_copy(update=updates)
        return updated
