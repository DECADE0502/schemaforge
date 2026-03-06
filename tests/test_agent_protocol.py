"""测试 schemaforge.agent.protocol

覆盖: AgentStep, AgentAction, QuestionItem, PatchOp, EvidenceRef
"""

from schemaforge.agent.protocol import (
    AgentAction,
    AgentStep,
    EvidenceRef,
    PatchOp,
    QuestionItem,
    RationalityIssue,
    ToolCallRequest,
)


class TestAgentStep:
    def test_tools_shortcut(self):
        step = AgentStep.tools(
            calls=[ToolCallRequest(tool_name="parse_pdf", arguments={"filepath": "x.pdf"})],
            message="解析PDF",
        )
        assert step.action == AgentAction.CALL_TOOLS
        assert len(step.tool_calls) == 1
        assert step.tool_calls[0].tool_name == "parse_pdf"

    def test_ask_shortcut(self):
        step = AgentStep.ask(
            questions=[QuestionItem(text="GND在第几脚?", answer_type="number")],
            message="需要确认引脚",
        )
        assert step.action == AgentAction.ASK_USER
        assert len(step.questions) == 1

    def test_draft_shortcut(self):
        step = AgentStep.draft(
            proposal={"device_name": "TPS54202"},
            message="器件草稿",
            checks=[RationalityIssue(rule_id="r1", severity="warning", message="注意散热")],
        )
        assert step.action == AgentAction.PRESENT_DRAFT
        assert step.proposal["device_name"] == "TPS54202"
        assert len(step.checks) == 1

    def test_done_shortcut(self):
        step = AgentStep.done("入库完成")
        assert step.action == AgentAction.FINALIZE
        assert step.message == "入库完成"

    def test_fail_shortcut(self):
        step = AgentStep.fail("解析失败")
        assert step.action == AgentAction.FAIL
        assert step.message == "解析失败"

    def test_full_creation(self):
        step = AgentStep(
            action=AgentAction.APPLY_PATCH,
            message="修改输出电容",
            patch_ops=[PatchOp(op="set", path="modules[0].parameters.c_out", value="47uF")],
            next_state="compiling",
        )
        assert step.action == AgentAction.APPLY_PATCH
        assert len(step.patch_ops) == 1
        assert step.next_state == "compiling"

    def test_serialization_roundtrip(self):
        step = AgentStep.tools(
            calls=[ToolCallRequest(tool_name="search_easyeda", arguments={"part_number": "AMS1117"})],
        )
        json_str = step.model_dump_json()
        step2 = AgentStep.model_validate_json(json_str)
        assert step2.action == AgentAction.CALL_TOOLS
        assert step2.tool_calls[0].tool_name == "search_easyeda"


class TestQuestionItem:
    def test_choice_question(self):
        q = QuestionItem(
            question_id="q1",
            text="选择封装",
            answer_type="choice",
            choices=["SOT-23-6", "SOT-223", "QFN-16"],
            required=True,
        )
        assert q.answer_type == "choice"
        assert len(q.choices) == 3

    def test_confirm_question(self):
        q = QuestionItem(
            text="确认 VIN 最大电压 36V？",
            answer_type="confirm",
            evidence="datasheet 第3页",
        )
        assert q.answer_type == "confirm"
        assert "datasheet" in q.evidence


class TestPatchOp:
    def test_set_op(self):
        op = PatchOp(op="set", path="modules[0].parameters.c_out", value="47uF", reason="用户要求")
        assert op.op == "set"
        assert op.value == "47uF"


class TestEvidenceRef:
    def test_pdf_evidence(self):
        ref = EvidenceRef(
            source_type="pdf",
            path="/tmp/TPS54202.pdf",
            page=3,
            summary="引脚表在第3页",
            confidence=0.85,
        )
        assert ref.source_type == "pdf"
        assert ref.confidence == 0.85

    def test_low_confidence(self):
        ref = EvidenceRef(
            source_type="ai_inferred",
            summary="AI推断封装为SOT-23-6",
            confidence=0.4,
        )
        assert ref.confidence < 0.5
