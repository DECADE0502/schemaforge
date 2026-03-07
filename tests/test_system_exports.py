"""Tests for system-level exports: instances, BOM, SPICE (T071-T080).

验证全局参考编号分配、BOM 导出（Markdown/CSV）、SPICE 网表生成。
约束覆盖: C71-C80。
"""

from __future__ import annotations

import csv
import io

from schemaforge.system.export_bom import (
    export_system_bom_csv,
    export_system_bom_markdown,
)
from schemaforge.system.export_spice import (
    export_system_spice,
    map_system_nets_to_spice_nodes,
)
from schemaforge.system.instances import (
    ComponentInstance,
    allocate_global_references,
    create_component_instances,
    stabilize_references_after_revision,
)
from schemaforge.system.models import (
    ModuleInstance,
    ModuleStatus,
    NetType,
    PortRef,
    ResolvedConnection,
    SystemDesignIR,
    SystemDesignRequest,
    SystemNet,
)


# ============================================================
# Helpers
# ============================================================


def _make_port(
    module_id: str,
    role: str,
    pin_name: str,
    net_class: NetType = NetType.SIGNAL,
) -> PortRef:
    """快捷创建 PortRef。"""
    return PortRef(
        module_id=module_id,
        port_role=role,
        pin_name=pin_name,
        net_class=net_class,
    )


def _make_buck_instance(module_id: str = "buck1") -> ModuleInstance:
    """创建已综合的 Buck 模块（含外围元件）。"""
    return ModuleInstance(
        module_id=module_id,
        role="降压",
        resolved_category="buck",
        resolved_ports={
            "VIN": _make_port(module_id, "power_in", "VIN", NetType.POWER),
            "VOUT": _make_port(module_id, "power_out", "VOUT", NetType.POWER),
            "GND": _make_port(module_id, "ground", "GND", NetType.GROUND),
            "SW": _make_port(module_id, "switch", "SW", NetType.POWER),
            "FB": _make_port(module_id, "feedback", "FB", NetType.SIGNAL),
        },
        parameters={"v_in": "24", "v_out": "5", "i_out": "2"},
        external_components=[
            {
                "role": "inductor",
                "ref_prefix": "L",
                "value": "10uH",
                "formula": "L = Vout * (1-D) / (fsw * 0.3 * Iout)",
                "evidence": "Buck 典型拓扑电感计算",
            },
            {
                "role": "input_cap",
                "ref_prefix": "C",
                "value": "10uF",
                "formula": "Cin >= 10uF",
                "evidence": "输入纹波电压估算",
            },
            {
                "role": "output_cap",
                "ref_prefix": "C",
                "value": "22uF",
                "formula": "Cout >= 22uF",
                "evidence": "输出纹波估算",
            },
            {
                "role": "boot_cap",
                "ref_prefix": "C",
                "value": "100nF",
                "formula": "Cboot = 100nF",
                "evidence": "自举电容典型值",
            },
            {
                "role": "fb_upper",
                "ref_prefix": "R",
                "value": "51k\u03a9",
                "formula": "Rupper = Rlower * (Vout/Vref - 1)",
                "evidence": "反馈分压网络上拉电阻",
            },
            {
                "role": "fb_lower",
                "ref_prefix": "R",
                "value": "10k\u03a9",
                "formula": "Rlower = 10k",
                "evidence": "反馈分压网络下拉电阻",
            },
            {
                "role": "diode",
                "ref_prefix": "D",
                "value": "SS34",
                "formula": "Schottky diode",
                "evidence": "续流二极管",
            },
        ],
        status=ModuleStatus.SYNTHESIZED,
    )


def _make_ldo_instance(module_id: str = "ldo1") -> ModuleInstance:
    """创建已综合的 LDO 模块。"""
    return ModuleInstance(
        module_id=module_id,
        role="稳压",
        resolved_category="ldo",
        resolved_ports={
            "VIN": _make_port(module_id, "power_in", "VIN", NetType.POWER),
            "VOUT": _make_port(module_id, "power_out", "VOUT", NetType.POWER),
            "GND": _make_port(module_id, "ground", "GND", NetType.GROUND),
        },
        parameters={"v_in": "5", "v_out": "3.3"},
        external_components=[
            {
                "role": "input_cap",
                "ref_prefix": "C",
                "value": "10uF",
                "formula": "Cin >= 10uF",
                "evidence": "LDO 输入去耦",
            },
            {
                "role": "output_cap",
                "ref_prefix": "C",
                "value": "22uF",
                "formula": "Cout >= 22uF",
                "evidence": "LDO 输出电容",
            },
        ],
        status=ModuleStatus.SYNTHESIZED,
    )


def _make_supply_connection(
    src_module: str,
    dst_module: str,
    net_name: str = "NET_5V",
) -> ResolvedConnection:
    """创建电源链连接。"""
    return ResolvedConnection(
        resolved_connection_id=f"{src_module}_to_{dst_module}",
        src_port=PortRef(
            module_id=src_module,
            port_role="power_out",
            pin_name="VOUT",
            net_class=NetType.POWER,
        ),
        dst_port=PortRef(
            module_id=dst_module,
            port_role="power_in",
            pin_name="VIN",
            net_class=NetType.POWER,
        ),
        net_name=net_name,
        rule_id="RULE_POWER_SUPPLY",
    )


def _make_ir(
    modules: dict[str, ModuleInstance],
    connections: list[ResolvedConnection] | None = None,
    nets: dict[str, SystemNet] | None = None,
) -> SystemDesignIR:
    """创建测试用 SystemDesignIR。"""
    if nets is None:
        nets = {}
    return SystemDesignIR(
        request=SystemDesignRequest(raw_text="test"),
        module_instances=modules,
        connections=connections or [],
        nets=nets,
    )


def _make_buck_ldo_ir() -> SystemDesignIR:
    """创建 Buck -> LDO 的完整 IR（含网络）。"""
    buck = _make_buck_instance()
    ldo = _make_ldo_instance()
    conn = _make_supply_connection("buck1", "ldo1", "NET_5V")

    gnd_net = SystemNet(
        net_id="GND",
        net_name="GND",
        net_type=NetType.GROUND,
        voltage_domain="0V",
        is_global=True,
        members=[
            _make_port("buck1", "ground", "GND", NetType.GROUND),
            _make_port("ldo1", "ground", "GND", NetType.GROUND),
        ],
    )
    power_net = SystemNet(
        net_id="NET_5V",
        net_name="NET_5V",
        net_type=NetType.POWER,
        voltage_domain="5V",
        members=[
            _make_port("buck1", "power_out", "VOUT", NetType.POWER),
            _make_port("ldo1", "power_in", "VIN", NetType.POWER),
        ],
    )

    return _make_ir(
        {"buck1": buck, "ldo1": ldo},
        [conn],
        {"GND": gnd_net, "NET_5V": power_net},
    )


# ============================================================
# T071: ComponentInstance 数据模型
# ============================================================


class TestComponentInstance:
    """T071: ComponentInstance 基本结构。"""

    def test_dataclass_fields(self) -> None:
        """ComponentInstance 包含所有必要字段。"""
        inst = ComponentInstance(
            ref="C1",
            module_id="buck1",
            role="input_cap",
            value="10uF",
            package="0805",
            description="输入电容",
        )
        assert inst.ref == "C1"
        assert inst.module_id == "buck1"
        assert inst.role == "input_cap"
        assert inst.value == "10uF"
        assert inst.package == "0805"
        assert inst.description == "输入电容"


# ============================================================
# T072: create_component_instances
# ============================================================


class TestCreateComponentInstances:
    """T072: 从 IR 收集器件实例。"""

    def test_collects_all_external_components(self) -> None:
        """收集所有模块的外围元件。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        # Buck: 7 external, LDO: 2 external = 9 total
        assert len(instances) == 9

    def test_each_has_module_id(self) -> None:
        """每个实例关联到正确的模块。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        module_ids = {inst.module_id for inst in instances}
        assert "buck1" in module_ids
        assert "ldo1" in module_ids

    def test_each_has_role(self) -> None:
        """每个实例有角色标记（C56）。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        for inst in instances:
            assert inst.role, f"Instance from {inst.module_id} missing role"

    def test_empty_ir_returns_empty(self) -> None:
        """空 IR 返回空列表。"""
        ir = _make_ir({})
        instances = create_component_instances(ir)
        assert instances == []


# ============================================================
# T073: allocate_global_references
# ============================================================


class TestAllocateGlobalReferences:
    """T073: 全局参考编号分配。"""

    def test_refs_globally_unique(self) -> None:
        """所有参考编号全局唯一（C71）。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)
        refs = [inst.ref for inst in instances]
        assert len(refs) == len(set(refs)), f"Duplicate refs: {refs}"

    def test_refs_sequential_per_prefix(self) -> None:
        """同一前缀内编号连续。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        # 按前缀分组
        groups: dict[str, list[int]] = {}
        for inst in instances:
            prefix = ""
            for i, ch in enumerate(inst.ref):
                if ch.isdigit():
                    prefix = inst.ref[:i]
                    num = int(inst.ref[i:])
                    groups.setdefault(prefix, []).append(num)
                    break

        # 每组应从 1 开始连续
        for prefix, nums in groups.items():
            nums.sort()
            assert nums[0] == 1, f"{prefix} should start at 1, got {nums[0]}"
            for i in range(len(nums) - 1):
                assert nums[i + 1] == nums[i] + 1, (
                    f"{prefix} not sequential: {nums}"
                )

    def test_no_ref_conflicts_across_modules(self) -> None:
        """不同模块的同类元件不会分到相同编号。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        cap_refs = [
            inst.ref for inst in instances if inst.ref.startswith("C")
        ]
        assert len(cap_refs) == len(set(cap_refs))

    def test_prefix_order_u_first(self) -> None:
        """IC (U) 编号优先于被动元件。"""
        ir = _make_buck_ldo_ir()
        # 给 buck 添加一个 device 使其产出 main_ic
        buck = ir.get_module("buck1")

        class FakeDevice:
            part_number = "TPS5430"
            package = "SOIC-8"
            spice_model = ""

        buck.device = FakeDevice()

        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        ic_instances = [i for i in instances if i.ref.startswith("U")]
        assert len(ic_instances) >= 1
        assert ic_instances[0].ref == "U1"


# ============================================================
# T074: stabilize_references_after_revision
# ============================================================


class TestStabilizeReferences:
    """T074: 修订后编号稳定性。"""

    def test_unchanged_components_keep_refs(self) -> None:
        """未变更的组件保留旧编号（C72）。"""
        old = [
            ComponentInstance("C1", "buck1", "input_cap", "10uF", "0805", ""),
            ComponentInstance("C2", "buck1", "output_cap", "22uF", "0805", ""),
            ComponentInstance("R1", "buck1", "fb_upper", "51k\u03a9", "0402", ""),
        ]

        # 新列表相同组件 + 一个新增
        new = [
            ComponentInstance("", "buck1", "input_cap", "10uF", "0805", ""),
            ComponentInstance("", "buck1", "output_cap", "22uF", "0805", ""),
            ComponentInstance("", "buck1", "fb_upper", "51k\u03a9", "0402", ""),
            ComponentInstance("", "buck1", "fb_lower", "10k\u03a9", "0402", ""),
        ]

        result = stabilize_references_after_revision(old, new)

        # 旧组件保持编号
        assert result[0].ref == "C1"
        assert result[1].ref == "C2"
        assert result[2].ref == "R1"

        # 新组件获得新编号
        assert result[3].ref == "R2"

    def test_removed_component_frees_number(self) -> None:
        """移除的组件编号不被新组件抢占（避免混淆）。"""
        old = [
            ComponentInstance("C1", "buck1", "input_cap", "10uF", "0805", ""),
            ComponentInstance("C2", "buck1", "output_cap", "22uF", "0805", ""),
        ]

        # C1 的组件被修改（value 变了）
        new = [
            ComponentInstance("", "buck1", "input_cap", "47uF", "0805", ""),
            ComponentInstance("", "buck1", "output_cap", "22uF", "0805", ""),
        ]

        result = stabilize_references_after_revision(old, new)

        # output_cap 保持 C2
        output_cap = next(i for i in result if i.role == "output_cap")
        assert output_cap.ref == "C2"

        # input_cap (changed value) gets new ref C3
        input_cap = next(i for i in result if i.role == "input_cap")
        assert input_cap.ref == "C3"

    def test_all_new_refs_unique(self) -> None:
        """修订后所有编号仍全局唯一。"""
        old = [
            ComponentInstance("C1", "m1", "input_cap", "10uF", "0805", ""),
            ComponentInstance("R1", "m1", "fb_upper", "10k\u03a9", "0402", ""),
        ]

        new = [
            ComponentInstance("", "m1", "input_cap", "10uF", "0805", ""),
            ComponentInstance("", "m1", "fb_upper", "10k\u03a9", "0402", ""),
            ComponentInstance("", "m2", "input_cap", "22uF", "0805", ""),
            ComponentInstance("", "m2", "fb_upper", "20k\u03a9", "0402", ""),
        ]

        result = stabilize_references_after_revision(old, new)
        refs = [i.ref for i in result]
        assert len(refs) == len(set(refs)), f"Duplicate refs: {refs}"


# ============================================================
# T075: BOM Markdown 导出
# ============================================================


class TestBomMarkdown:
    """T075: Markdown BOM 表生成。"""

    def test_has_all_components(self) -> None:
        """BOM 表包含所有组件。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        md = export_system_bom_markdown(instances, ir)

        # 每个实例的 ref 应出现在 BOM 中
        for inst in instances:
            assert inst.ref in md, f"{inst.ref} not in BOM"

    def test_has_table_header(self) -> None:
        """BOM 有正确的表头。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        md = export_system_bom_markdown(instances, ir)
        assert "位号" in md
        assert "名称" in md
        assert "数值" in md
        assert "封装" in md
        assert "所属模块" in md
        assert "备注" in md

    def test_has_component_count(self) -> None:
        """BOM 末尾有器件总数。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        md = export_system_bom_markdown(instances, ir)
        assert f"共 {len(instances)} 个器件" in md

    def test_unresolved_warning(self) -> None:
        """含未解析模块时有警告（C80）。"""
        buck = _make_buck_instance()
        pending = ModuleInstance(
            module_id="unknown1",
            role="未知模块",
            resolved_category="unknown",
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number="XYZ123",
        )
        ir = _make_ir({"buck1": buck, "unknown1": pending})
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        md = export_system_bom_markdown(instances, ir)
        assert "警告" in md
        assert "XYZ123" in md


# ============================================================
# T076: BOM CSV 导出
# ============================================================


class TestBomCsv:
    """T076: CSV BOM 导出。"""

    def test_csv_parseable(self) -> None:
        """CSV 可被标准库解析。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        csv_str = export_system_bom_csv(instances)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)

        # 表头 + 数据行
        assert len(rows) == 1 + len(instances)
        assert rows[0] == ["Ref", "Value", "Package", "Module", "Description"]

    def test_csv_has_all_refs(self) -> None:
        """CSV 包含所有组件的 ref。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        csv_str = export_system_bom_csv(instances)
        for inst in instances:
            assert inst.ref in csv_str

    def test_csv_module_column(self) -> None:
        """CSV 每行有正确的模块 ID。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        csv_str = export_system_bom_csv(instances)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)

        for row in rows[1:]:  # 跳过表头
            assert row[3] in ("buck1", "ldo1")


# ============================================================
# T078: SPICE 网表生成
# ============================================================


class TestSpiceExport:
    """T078: SPICE 网表生成。"""

    def test_spice_has_end(self) -> None:
        """SPICE 网表以 .end 结尾。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        spice = export_system_spice(ir, instances)
        assert spice.strip().endswith(".end")

    def test_spice_gnd_is_zero(self) -> None:
        """GND 映射为 SPICE 节点 0（C73）。"""
        ir = _make_buck_ldo_ir()
        node_map = map_system_nets_to_spice_nodes(ir)
        assert node_map["GND"] == "0"

    def test_spice_shared_node_buck_vout_ldo_vin(self) -> None:
        """Buck VOUT 和 LDO VIN 在同一网络时使用同一 SPICE 节点（C74）。"""
        ir = _make_buck_ldo_ir()
        node_map = map_system_nets_to_spice_nodes(ir)

        # NET_5V 包含 buck1.VOUT 和 ldo1.VIN，应是同一节点
        assert "NET_5V" in node_map
        spice_node = node_map["NET_5V"]

        # SPICE 网表中此节点应出现
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)
        spice = export_system_spice(ir, instances)
        assert spice_node in spice

    def test_spice_has_passive_components(self) -> None:
        """SPICE 网表包含被动元件。"""
        ir = _make_buck_ldo_ir()
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        spice = export_system_spice(ir, instances)

        # 至少有 R, C, L 元件
        assert any(
            line.startswith("R") for line in spice.split("\n")
            if not line.startswith("*")
        )
        assert any(
            line.startswith("C") for line in spice.split("\n")
            if not line.startswith("*")
        )
        assert any(
            line.startswith("L") for line in spice.split("\n")
            if not line.startswith("*")
        )

    def test_spice_missing_model_annotated(self) -> None:
        """无 SPICE 模型的 IC 有注释标注（C75）。"""
        buck = _make_buck_instance()

        class FakeDevice:
            part_number = "NOMODEL_IC"
            package = "QFN-8"
            spice_model = ""  # 无模型

        buck.device = FakeDevice()

        ir = _make_ir(
            {"buck1": buck},
            nets={
                "GND": SystemNet(
                    net_id="GND", net_name="GND",
                    net_type=NetType.GROUND, is_global=True,
                ),
            },
        )
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        spice = export_system_spice(ir, instances)
        assert "WARNING" in spice
        assert "No SPICE model" in spice

    def test_spice_unresolved_module_warning(self) -> None:
        """含未解析模块时 SPICE 有警告（C80）。"""
        buck = _make_buck_instance()
        pending = ModuleInstance(
            module_id="broken1",
            role="损坏模块",
            resolved_category="unknown",
            status=ModuleStatus.NEEDS_ASSET,
            missing_part_number="MISSING_CHIP",
        )
        ir = _make_ir(
            {"buck1": buck, "broken1": pending},
            nets={
                "GND": SystemNet(
                    net_id="GND", net_name="GND",
                    net_type=NetType.GROUND, is_global=True,
                ),
            },
        )
        instances = create_component_instances(ir)
        instances = allocate_global_references(instances)

        spice = export_system_spice(ir, instances)
        assert "MISSING" in spice


# ============================================================
# T079: map_system_nets_to_spice_nodes
# ============================================================


class TestMapNetsToSpiceNodes:
    """T079: 网络到 SPICE 节点映射。"""

    def test_gnd_maps_to_zero(self) -> None:
        """GND 网络映射为 '0'。"""
        ir = _make_buck_ldo_ir()
        mapping = map_system_nets_to_spice_nodes(ir)
        assert mapping["GND"] == "0"

    def test_power_net_preserved(self) -> None:
        """电源网络名称保留。"""
        ir = _make_buck_ldo_ir()
        mapping = map_system_nets_to_spice_nodes(ir)
        assert "NET_5V" in mapping
        assert mapping["NET_5V"] == "NET_5V"

    def test_default_gnd_always_present(self) -> None:
        """即使 IR 无 GND 网络，映射中仍有 GND。"""
        ir = _make_ir({}, nets={})
        mapping = map_system_nets_to_spice_nodes(ir)
        assert mapping["GND"] == "0"


# ============================================================
# 集成测试
# ============================================================


class TestFullPipelineIntegration:
    """T080: 完整导出管线集成。"""

    def test_full_pipeline_buck_ldo(self) -> None:
        """Buck->LDO 完整管线：实例收集 -> 编号 -> BOM -> SPICE。"""
        ir = _make_buck_ldo_ir()

        # Step 1: 收集
        instances = create_component_instances(ir)
        assert len(instances) > 0

        # Step 2: 编号
        instances = allocate_global_references(instances)
        refs = [i.ref for i in instances]
        assert len(refs) == len(set(refs))

        # Step 3: BOM
        md = export_system_bom_markdown(instances, ir)
        assert "位号" in md
        csv_str = export_system_bom_csv(instances)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) > 1

        # Step 4: SPICE
        spice = export_system_spice(ir, instances)
        assert ".end" in spice
        assert "0" in spice  # GND node

    def test_revision_stability_integration(self) -> None:
        """多轮修改后编号稳定性（C72, C99）。"""
        ir = _make_buck_ldo_ir()

        # 第一轮
        old_instances = create_component_instances(ir)
        old_instances = allocate_global_references(old_instances)
        old_refs = {(i.module_id, i.role, i.value): i.ref for i in old_instances}

        # 第二轮（相同 IR，模拟重新综合）
        new_instances = create_component_instances(ir)
        new_instances = stabilize_references_after_revision(
            old_instances, new_instances,
        )

        # 所有旧组件应保持相同编号
        for inst in new_instances:
            key = (inst.module_id, inst.role, inst.value)
            if key in old_refs:
                assert inst.ref == old_refs[key], (
                    f"{key} changed from {old_refs[key]} to {inst.ref}"
                )
