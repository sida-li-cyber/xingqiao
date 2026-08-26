const fs = require("fs");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun,
        Header, Footer, AlignmentType, LevelFormat, HeadingLevel, BorderStyle,
        WidthType, ShadingType, PageNumber, PageBreak, VerticalAlign } = require("docx");

// ---------- constants ----------
const F = { ascii: "Arial", hAnsi: "Arial", eastAsia: "Microsoft YaHei" };
const BLUE = "1F4E79", MIDBLUE = "2E75B6", LIGHT = "DEEAF6", ALT = "F2F7FB", GRAY = "7F7F7F", BOXFILL = "F5F9FD";
const CW = 9026; // A4 content width in DXA (11906 - 2*1440)
const thin = { style: BorderStyle.SINGLE, size: 4, color: "C9C9C9" };
const tborders = { top: thin, bottom: thin, left: thin, right: thin };
const cellMargins = { top: 70, bottom: 70, left: 110, right: 110 };

// ---------- helpers ----------
const t = (text, opts = {}) => new TextRun({ text, font: F, ...opts });

function body(text, opts = {}) {
  return new Paragraph({
    children: [t(text)],
    spacing: { line: 312, after: 60 },
    indent: { firstLine: 420 },
    alignment: AlignmentType.JUSTIFIED,
    ...opts
  });
}
function bodyRuns(runs, opts = {}) {
  return new Paragraph({
    children: runs,
    spacing: { line: 312, after: 60 },
    indent: { firstLine: 420 },
    alignment: AlignmentType.JUSTIFIED,
    ...opts
  });
}
function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: true, children: [t(text)] });
}
function h1First(text) { // no page break (used right after cover's explicit break)
  return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [t(text)] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [t(text)] });
}
function h3(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_3, children: [t(text)] });
}
function bullet(text, bold0 = null) {
  const runs = [];
  if (bold0) runs.push(t(bold0, { bold: true }));
  runs.push(t(text));
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs,
    spacing: { line: 300, after: 40 }, alignment: AlignmentType.JUSTIFIED });
}
function bulletRuns(runs) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs,
    spacing: { line: 300, after: 40 }, alignment: AlignmentType.JUSTIFIED });
}
function numbered(text, bold0 = null) {
  const runs = [];
  if (bold0) runs.push(t(bold0, { bold: true }));
  runs.push(t(text));
  return new Paragraph({ numbering: { reference: "numbers", level: 0 }, children: runs,
    spacing: { line: 300, after: 40 }, alignment: AlignmentType.JUSTIFIED });
}
function cap(text) {
  return new Paragraph({ children: [t(text, { size: 18, color: GRAY })],
    alignment: AlignmentType.CENTER, spacing: { before: 60, after: 160 } });
}
function tcPara(text, opts = {}) {
  return new Paragraph({ children: [t(text, { size: 20 })], spacing: { line: 276, before: 20, after: 20 }, ...opts });
}
function makeTable({ headers, rows, widths, align = null, fontSize = 20, headerFill = BLUE }) {
  const hRow = new TableRow({
    cantSplit: true,
    tableHeader: true,
    children: headers.map((htext, i) => new TableCell({
      borders: tborders, margins: cellMargins,
      width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: headerFill, type: ShadingType.CLEAR },
      verticalAlign: VerticalAlign.CENTER,
      children: [new Paragraph({ alignment: AlignmentType.CENTER,
        children: [t(htext, { size: fontSize, bold: true, color: "FFFFFF" })] })]
    }))
  });
  const bodyRows = rows.map((r, ri) => new TableRow({
    cantSplit: true,
    children: r.map((c, ci) => new TableCell({
      borders: tborders, margins: cellMargins,
      width: { size: widths[ci], type: WidthType.DXA },
      shading: { fill: ri % 2 === 1 ? ALT : "FFFFFF", type: ShadingType.CLEAR },
      verticalAlign: VerticalAlign.CENTER,
      children: [new Paragraph({
        alignment: (align && align[ci]) ? align[ci] : AlignmentType.LEFT,
        children: [t(String(c), { size: fontSize })] })]
    }))
  }));
  return new Table({
    width: { size: CW, type: WidthType.DXA },
    columnWidths: widths,
    rows: [hRow, ...bodyRows]
  });
}
function metricCards(items) { // items: [{num, label}]
  return new Table({
    width: { size: CW, type: WidthType.DXA },
    columnWidths: items.map(() => Math.floor(CW / items.length)),
    rows: [new TableRow({
      cantSplit: true,
      children: items.map(it => new TableCell({
        borders: tborders, margins: { top: 120, bottom: 120, left: 80, right: 80 },
        width: { size: Math.floor(CW / items.length), type: WidthType.DXA },
        shading: { fill: BOXFILL, type: ShadingType.CLEAR },
        verticalAlign: VerticalAlign.CENTER,
        children: [
          new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
            children: [t(it.num, { size: 30, bold: true, color: BLUE })] }),
          new Paragraph({ alignment: AlignmentType.CENTER,
            children: [t(it.label, { size: 17, color: GRAY })] })
        ]
      }))
    })]
  });
}
function box(title, text) {
  return new Table({
    width: { size: CW, type: WidthType.DXA },
    columnWidths: [CW],
    rows: [new TableRow({
      cantSplit: true,
      children: [new TableCell({
        borders: { top: thin, bottom: thin, right: thin,
          left: { style: BorderStyle.SINGLE, size: 24, color: BLUE } },
        margins: { top: 100, bottom: 100, left: 160, right: 140 },
        width: { size: CW, type: WidthType.DXA },
        shading: { fill: BOXFILL, type: ShadingType.CLEAR },
        children: [
          new Paragraph({ spacing: { after: 40 }, children: [t(title, { bold: true, size: 21, color: BLUE })] }),
          new Paragraph({ spacing: { line: 300 }, alignment: AlignmentType.JUSTIFIED, children: [t(text, { size: 20 })] })
        ]
      })]
    })]
  });
}
const spacer = (h = 120) => new Paragraph({ children: [t(" ", { size: 8 })], spacing: { after: h } });

// ---------- document content ----------
const children = [];

// ===== COVER =====
children.push(new Paragraph({
  alignment: AlignmentType.CENTER, spacing: { before: 0, after: 0 },
  children: [new ImageRun({
    type: "jpg",
    data: fs.readFileSync("cover_final.jpg"),
    transformation: { width: 540, height: 267 },
    altText: { title: "星桥平台封面", description: "卫星星座网络环绕地球的科技插画", name: "cover" }
  })]
}));
children.push(new Table({
  width: { size: CW, type: WidthType.DXA },
  columnWidths: [CW],
  rows: [new TableRow({
    children: [new TableCell({
      borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" } },
      margins: { top: 160, bottom: 160, left: 200, right: 200 },
      width: { size: CW, type: WidthType.DXA },
      shading: { fill: BLUE, type: ShadingType.CLEAR },
      children: [
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
          children: [t("项 目 计 划 书", { size: 52, bold: true, color: "FFFFFF" })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
          children: [t("星桥 · 卫星网络虚拟仿真实验平台", { size: 34, bold: true, color: "FFFFFF" })] }),
        new Paragraph({ alignment: AlignmentType.CENTER,
          children: [t("StarBridge — 面向高校的轻量级空天地海网络仿真教学平台", { size: 20, color: "D6E4F0" })] })
      ]
    })]
  })]
}));
children.push(spacer(160));
const coverInfo = [
  ["参赛赛道", "中国国际大学生创新大赛 · 高教主赛道 · 本科生创意组"],
  ["项目名称", "星桥 · 卫星网络虚拟仿真实验平台"],
  ["依托院校", "哈尔滨工业大学（深圳）"],
  ["项目来源", "校级大一年度项目「空天海网络可视化实时交互仿真演示系统」"],
  ["团队负责人", "李思达（基础学部）"],
  ["团队成员", "符浩原、李璟哲"],
  ["指导教师", "焦健 教授（信息科学与技术学院）"],
  ["编制日期", "2026 年 8 月"]
];
children.push(new Table({
  width: { size: 7000, type: WidthType.DXA },
  columnWidths: [1800, 5200],
  alignment: AlignmentType.CENTER,
  rows: coverInfo.map(r => new TableRow({
    cantSplit: true,
    children: [
      new TableCell({ borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" } },
        margins: cellMargins, width: { size: 1800, type: WidthType.DXA },
        children: [new Paragraph({ alignment: AlignmentType.LEFT, children: [t(r[0], { size: 22, bold: true, color: BLUE })] })] }),
      new TableCell({ borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" } },
        margins: cellMargins, width: { size: 5200, type: WidthType.DXA },
        children: [new Paragraph({ alignment: AlignmentType.LEFT, children: [t(r[1], { size: 22 })] })] })
    ]
  }))
}));
children.push(spacer(120));
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [t("本计划书中的平台技术指标均为项目实测数据，附可复现测试脚本；", { size: 18, color: GRAY })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [t("市场与财务数据为基于公开资料与明示假设的测算，详见附录。", { size: 18, color: GRAY })] }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 摘要 =====
children.push(h1First("摘  要"));
children.push(body("「星桥」是一个面向高校教学的轻量级卫星网络虚拟仿真实验平台：学生在浏览器中的三维地球上，实时观看并亲手操作一个真实的低轨（LEO）卫星网络——上千颗卫星按轨道运动、数据包在星间链路上逐跳转发、队列在拥塞中堆积、丢包在切换瞬间尖峰涌现。它把过去只有科研工作者才能使用的包级网络仿真，变成了一台“开箱即用、自带标准答案”的教学实验装置。"));
children.push(body("平台源自团队在哈尔滨工业大学（深圳）承担的大一年度项目「空天海网络可视化实时交互仿真演示系统」。团队没有在重型仿真框架上做加法，而是自研了约 500 行代码的轻量级包级离散事件仿真引擎（DES），配合 FastAPI + WebSocket 实时后端与 CesiumJS 三维前端，构成“仿真核心—实时后端—浏览器前端”三层解耦架构：无需编译任何 C++ 代码，一台 4 GB 内存的普通笔记本电脑即可运行，千星级（1584 颗卫星）星座仿真实测约 34 ticks/s、浏览器渲染 48 帧/秒。"));
children.push(body("更重要的是“可信”：平台每一个实验都自带理论答案——端到端时延与几何传播理论值精确对账、拥塞场景与 M/D/1 排队论模型误差仅 1.7%、包守恒不变量（生成 = 送达 + 丢弃 + 在途）在全部测试场景中精确成立。教师第一次可以像批改数学题一样“批改”网络实验；学生先预测、后验证，完成从背诵结论到理解机制的跨越。"));
children.push(body("商业航天已被写入政府工作报告，工信部《制造业人才发展规划指南》预测航空航天装备领域人才缺口达 47.5 万人，而高校卫星网络教学长期面临“工具重、成本高、看不见、无答案”四大痛点。星桥以“轻量实时 + 可验证 + 开源生态”切入高校虚拟仿真实验教学市场，采用“开源社区免费 + 试点订阅 + 课程共建 + 企业培训”的轻量商业模式；发展路径上先本校课程验证、再区域小范围试点，商业化以覆盖成本、验证付费意愿为目标，小步迭代，不预设快速规模化。"));
children.push(spacer(60));
children.push(cap("表 0-1  项目关键指标速览（全部为实测值）"));
children.push(metricCards([
  { num: "1584 星", label: "千星级实时仿真（约 34 ticks/s）" },
  { num: "48 FPS", label: "浏览器三维渲染帧率（1584 星）" },
  { num: "1.7%", label: "M/D/1 排队论对账误差" },
  { num: "0 编译", label: "纯 Python，4GB 内存即可运行" }
]));
children.push(spacer(80));
children.push(metricCards([
  { num: "1050 万包", label: "300 秒全管线仿真零丢包送达" },
  { num: "1160 ticks/s", label: "DES 引擎长时压测吞吐" },
  { num: "SHA-256", label: "文件传输逐字节校验一致" },
  { num: "16+ 项", label: "固定随机种子可复现测试" }
]));
children.push(spacer(60));
children.push(body("关键词：卫星网络；虚拟仿真实验；离散事件仿真；LEO 星座；新工科；实时可视化"));

// ===== 第一章 项目概述 =====
children.push(h1("第一章  项目概述"));
children.push(h2("1.1  一句话定位"));
children.push(box("星桥 · 卫星网络虚拟仿真实验平台",
  "把卫星网络实验从机房搬进浏览器：一台普通笔记本、五分钟部署，学生即可在三维地球上亲手“看见”数据包如何在 1584 颗卫星组成的星座中逐跳流动——并且每一个实验结果都可以和理论公式对账验证。"));
children.push(spacer(40));
children.push(h2("1.2  项目缘起"));
children.push(body("2025 年秋，团队三名大一新生在哈尔滨工业大学（深圳）立项大一年度项目「空天海网络可视化实时交互仿真演示系统」，源于一个朴素的困惑：卫星互联网已是新闻热词，Starlink、千帆、GW 星座动辄上千颗卫星，但在课堂上，“星间路由怎么走”“切换为什么会掉线”只能靠公式和静态图想象——现有的仿真工具要么是科研级的 ns-3（编译一次要 10~30 分钟、学习曲线陡峭），要么是价格高昂的商业软件，没有一款为“教学生”而设计。"));
children.push(body("一年间，团队完成了协议地基、包级仿真核心、多域流、实时解耦、包级可视化、校验加固、千星级扩展七个阶段的研发，形成了一套完整可运行、可验证、可复现的系统。本项目即该研究成果面向教育场景的轻量化产品化版本：保留经理论对账的包级仿真内核，剥离科研专用复杂度，围绕“教师好开课、学生好上手、结果可评分”重新组织产品形态，命名为「星桥」——寓意为空天地海之间架设一座通往知识的桥梁。"));
children.push(h2("1.3  项目基本信息"));
children.push(cap("表 1-1  项目基本信息"));
children.push(makeTable({
  headers: ["条目", "内容"],
  widths: [2200, 6826],
  rows: [
    ["项目名称", "星桥 · 卫星网络虚拟仿真实验平台（StarBridge）"],
    ["项目类型", "教育科技 / 虚拟仿真实验教学软件（高教主赛道 · 本科生创意组）"],
    ["依托单位", "哈尔滨工业大学（深圳）"],
    ["项目来源", "校级大一年度项目「空天海网络可视化实时交互仿真演示系统」（已通过中期检查）"],
    ["团队构成", "本科生 3 人（李思达、符浩原、李璟哲），指导教师焦健教授"],
    ["技术形态", "自研 Python 离散事件仿真引擎 + FastAPI/WebSocket 实时后端 + CesiumJS 浏览器三维前端"],
    ["开源协议", "自研核心采用 MIT 协议开源（基于 MIT 协议的 Hypatia 框架增强开发，主链路未使用 GPL 组件）"],
    ["当前状态", "系统已完整可用：千星级仿真、六大教学实验、文件传输实战、16+ 项自动化测试全部通过"]
  ]
}));
children.push(h2("1.4  本计划书结构"));
children.push(body("本计划书按照中国国际大学生创新大赛高教主赛道（本科生创意组）评审要点组织：第二章至第四章回应“项目创新”（痛点—方案—技术，30 分），第五章至第九章回应“产业价值”（市场—商业模式—营销—规划—财务，25 分），第十章回应“团队协作”（15 分），第十一、十二章回应“个人成长”中的立德树人与社会价值维度（30 分，其余个人成长要点贯穿全文），并在附录给出评审维度对照表与全部数据来源。"));

// ===== 第二章 背景与痛点 =====
children.push(h1("第二章  背景与痛点分析"));
children.push(h2("2.1  产业背景：商业航天进入爆发期"));
children.push(body("卫星互联网已成为全球战略竞争的制高点。我国“十四五”规划将卫星互联网纳入新型基础设施范畴；2024 年，“商业航天”首次写入政府工作报告，与低空经济并列为新增长引擎。产业侧的数据更为直观：2025 年全国新增商业航天企业 83 家，同比增长 277%；截至 2026 年 3 月，全国现存商业航天相关企业已达 9.7 万家。Starlink 在轨卫星超过 7000 颗，我国千帆星座、GW 星座相继进入批量组网阶段——卫星网络正从“单星工程”走向“星座运营”，网络化、系统化能力成为行业新刚需。"));
children.push(h2("2.2  人才缺口：会“星座组网”的人严重不足"));
children.push(body("产业爆发直接撞上了人才天花板。工业和信息化部《制造业人才发展规划指南》预测，至 2025 年航空航天装备领域人才缺口达 47.5 万人，而行业现有人才总量仅约 30 万人；另据行业人才报告，2025 年卫星制造、发射、地面网络与终端应用等核心环节人才缺口约 2.8 万人，其中星座调度、星上处理、网络系统设计等“组网类”岗位缺口尤为突出。与岗位需求形成反差的是：卫星网络是典型的动态复杂系统（时变拓扑、频繁切换、多域融合），仅靠公式推导难以建立直觉，必须依赖实验环境反复动手——而这恰恰是当前教学体系最薄弱的环节。"));
children.push(h2("2.3  政策东风：虚拟仿真实验是国家认证的课程形态"));
children.push(body("教育部自 2017 年起持续推进示范性虚拟仿真实验教学项目建设，并将其纳入国家级一流本科课程（五类“金课”之一）；教育数字化战略行动进一步把优质数字实验资源列为建设重点。“虚拟仿真实验教学课程”已成为高校新工科建设、专业认证与教学成果奖申报的重要抓手。在国家实验空间平台（ilab-x.com）上，卫星通信类虚拟仿真实验已有多个立项项目，证明需求真实存在——但现有项目多聚焦单星链路预算、载荷波束等“点”上问题，面向“星座组网与网络行为”的实时交互仿真仍是空白。"));
children.push(h2("2.4  核心痛点：卫星网络教学“三难一缺”"));
children.push(cap("表 2-1  现有工具与教学需求的错位"));
children.push(makeTable({
  headers: ["痛点", "具体表现", "现有工具的局限"],
  widths: [1500, 3900, 3626],
  rows: [
    ["工具重", "开设一次包级仿真实验，教师需提前数天准备编译环境", "ns-3 需 C++ 编译链与 OpenMPI，单次编译 10~30 分钟，报错门槛高；学习曲线以“月”计"],
    ["成本高", "商业仿真软件按授权收费，机房批量部署成本高", "STK 等商业软件授权昂贵，教学版功能受限；自研实验装置动辄数十万元"],
    ["看不见", "星座是动态系统，但仿真输出是 CSV 与日志，网络行为无法直观看", "传统仿真面向离线分析，结果回放滞后；学生“跑完了也不知道网络里发生了什么”"],
    ["缺答案", "实验结果无标准答案，教师无法客观评分，学生无法自我校验", "仿真参数与理论公式脱节，实验报告“抄参数”现象普遍，动手价值打折"]
  ]
}));
children.push(body("四个痛点层层递进：工具重、成本高把实验课挡在了大多数普通高校门外；看不见使学生失去对动态网络的最重要直觉；缺答案则让实验教学质量无从评价。星桥的每一项设计，都对应着击穿其中一个痛点。"));
children.push(h2("2.5  目标用户画像"));
children.push(cap("表 2-2  目标用户与核心诉求"));
children.push(makeTable({
  headers: ["用户群体", "典型场景", "核心诉求"],
  widths: [2200, 3900, 2926],
  rows: [
    ["高校教师（通信 / 电子信息 / 航空航天）", "《卫星通信》《计算机网络》《空天地一体化网络》课程的实验环节；虚拟仿真一流课程申报", "零门槛开课、实验自带评分依据、可直接用于课程建设申报"],
    ["本科生 / 研究生", "课程实验、课程设计、科创竞赛入门（如大创、挑战杯）", "个人电脑可运行、现象直观、结果可验证"],
    ["职业院校（航天 / 通信类专业群）", "卫星互联网产业人才培养、订单班实训", "低成本批量部署、贴近产业岗位技能"],
    ["科普场馆 / 中学航天社团", "航天科普展项、研学活动", "视觉冲击力强、交互安全、可离线运行"],
    ["商业航天企业", "新员工入职培训、售前演示", "快速建立星座网络全局认知、可定制场景"]
  ]
}));

// ===== 第三章 解决方案 =====
children.push(h1("第三章  解决方案与产品"));
children.push(h2("3.1  总体架构：三层解耦的轻量系统"));
children.push(body("星桥采用“仿真核心 — 实时后端 — 浏览器前端”三层解耦架构。仿真、传输、渲染三种频率彻底分离：仿真引擎可快于或慢于真实时间运行，后端按固定节拍转发状态快照，前端用插值把 5 Hz 的离散位置流平滑为 60 帧的连续运动。任何一层都可以独立替换——这既是工程上的可维护性，也是教学上的可扩展性（教师可以把仿真核心换成自己的数据源）。"));
children.push(cap("表 3-1  星桥三层架构"));
children.push(makeTable({
  headers: ["层级", "技术实现", "职责", "规模量级"],
  widths: [1700, 3000, 2900, 1426],
  rows: [
    ["仿真核心层", "自研 Python 离散事件仿真引擎（约 500 行核心代码）", "星座轨道、链路可见性、包级转发、排队与丢包、指标采集", "1584 星 / 约 34 ticks/s"],
    ["实时后端层", "FastAPI + WebSocket（约 500 行，6 个模块）", "状态广播、命令转发、文件传输数据面；纯 JSON 透明转发", "稳态单帧 < 50 KB"],
    ["浏览器前端层", "CesiumJS 三维地球 + 原生 JS 模块（5 个模块）", "三维渲染、链路着色、时序图表、交互控制、实验面板", "1584 星 48 FPS"]
  ]
}));
children.push(body("数据流：仿真核心以 5~10 Hz 推送状态快照（卫星位置、链路指标、文件传输进度），后端广播至全部浏览器客户端；学生在前端的每一次操作（播放、倍速、跳转、切换星座与场景、发送文件）都作为命令经后端转发回仿真核心实时生效——形成完整的双向交互闭环。"));
children.push(h2("3.2  六大产品功能"));
children.push(h3("F1  轻量级包级仿真引擎"));
children.push(body("自研离散事件仿真引擎以事件队列驱动真实的数据包转发：每一跳的传播时延由真实几何斜距除以光速计算，发送时延等于包长除以带宽，队列满则丢包。链路利用率、端到端时延、抖动、丢包率等全部指标均由真实数据包“涌现”，而非人工设定的假数据——这是与绝大多数“动画演示型”教学软件的本质区别。"));
children.push(h3("F2  实时三维可视化"));
children.push(body("浏览器中的三维地球上，数千颗卫星随轨道实时运动；星间链路按利用率绿—黄—红渐变着色；发光脉冲沿链路流动直观呈现数据流向；时序图表实时绘制吞吐、时延、丢包曲线。点击任意卫星、链路或传输任务，即可查看其随时间演化的详细指标。"));
children.push(h3("F3  交互式实验控制"));
children.push(body("播放 / 暂停 / 0.1~10 倍速 / 时间轴任意跳转；一键切换仿真场景（理想、商用、天气、切换、极端五档信道条件）与星座（Starlink 五壳层、Kuiper、Telesat）；支持节点筛选与聚焦。学生可以在任意时刻“暂停世界”观察一个包的处境——这是真实卫星网络永远做不到的事。"));
children.push(h3("F4  空天地海多域网络"));
children.push(body("平台原生支持卫星、无人机、船舶、地面站四类节点混合组网：无人机编队回传、船舶对岸通信、地面站接入共同跑在同一个包级仿真中，并支持任意终端之间的端到端组合（如船舶发往一颗移动中的卫星）。多域融合对应我国天地一体化信息网络重大工程方向，也是差异化竞争力所在。"));
children.push(h3("F5  文件传输实战"));
children.push(body("学生可上传任意文件，指定源端（如某架无人机）、目的端（如北京站）、优先级与速率上限，文件被切分为分片注入仿真网络，经逐跳转发、遭遇排队与丢包、必要时 ARQ 自动重传，最终在目的端还原——系统以 SHA-256 逐字节校验送达文件与原文件完全一致。传输全程可在三维地球上追踪路径、进度、吞吐与重传次数。这把抽象的“可靠传输协议”变成了一次亲手完成的任务。"));
children.push(h3("F6  理论对账与自动校验"));
children.push(body("平台内置对账体系：每个实验场景都附带理论基准值（传播时延公式、M/D/1 排队模型、守恒不变量），仿真结果自动与之比对并输出误差。教师可直接引用对账结果作为评分依据；所有测试脚本固定随机种子，任何人在任何机器上重复运行都能得到一致结果。"));
children.push(h2("3.3  六大教学实验设计"));
children.push(body("依托引擎能力，平台首批设计了六个由浅入深的实验，覆盖《计算机网络》《卫星通信》《排队论》等课程的核心知识点，每个实验均自带理论答案与观测点："));
children.push(cap("表 3-2  首批六大实验（理论答案均为实测对账结果）"));
children.push(makeTable({
  headers: ["实验", "学生做什么", "对账的理论答案（实测）"],
  widths: [2350, 3426, 3250],
  rows: [
    ["E1 时延分解与对账", "发送探测流，测量端到端时延，与“传播 + 发送 + 排队”理论分解值对比", "3 跳轻载端到端 21.037 ms，与理论值精确吻合；逐跳分解误差 ≤ 0.075 ms"],
    ["E2 拥塞与排队论", "逐步加大负载，绘制“负载—时延”曲线，观察排队爆发", "瓶颈链路 ρ=0.8 时实测 23.60 ms vs M/D/1 理论 24.00 ms，误差 1.7%"],
    ["E3 切换丢包观测", "在负载上行链路移除瞬间观测丢包尖峰", "尖峰 201 包 = 200 在队 + 1 在途，与计数器精确一致"],
    ["E4 QoS 优先级", "同一瓶颈下同时发送测控流（高优先）与批量流（尽力）", "高优先丢包 0.000 / 时延 35 ms，尽力丢包 0.605 / 时延 1065 ms"],
    ["E5 可靠文件传输", "在高丢包场景传输文件，观察 ARQ 重传与最终校验", "≥10 MB 文件 SHA-256 逐字节一致；移动目的端切换链路仍完整送达"],
    ["E6 多域组网设计", "为无人机与船舶规划接入方案，评估端到端性能", "72 星 + 8 机 + 10 船 + 15 站全网 300 秒送达约 105 万包、零非预期丢包"]
  ]
}));
children.push(h2("3.4  使用流程：从安装到实验报告的四步"));
children.push(numbered("安装依赖（pip 一条命令，无任何编译步骤），双击启动脚本，浏览器自动打开平台；", "①  开箱："));
children.push(numbered("教师从实验库选择实验（或自定义场景参数），一键下发到学生端；", "②  开课："));
children.push(numbered("学生在三维地球上一边操作、一边观察现象，平台同步记录全部指标；", "③  实验："));
children.push(numbered("系统自动生成含理论对账误差的实验报告，教师按对账结果评分。", "④  报告："));
children.push(h2("3.5  与现有方案的对比"));
children.push(cap("表 3-3  典型方案对比"));
children.push(makeTable({
  headers: ["维度", "ns-3 / Hypatia（科研开源）", "STK 等商业软件", "传统虚拟仿真实验", "星桥平台"],
  widths: [1450, 1950, 1900, 1900, 1826],
  fontSize: 18,
  rows: [
    ["部署门槛", "需 C++ 编译链，编译 10~30 分钟", "安装包大，教学授权受限", "网页或客户端，较易", "纯 Python，pip 安装，零编译"],
    ["硬件要求", "工作站级", "中高端 PC", "一般", "普通笔记本（4 GB 内存）"],
    ["实时交互", "无内置，偏离线批处理", "动画回放为主", "以动画演示为主", "实时双向交互，可暂停/倍速/跳转"],
    ["包级网络仿真", "有（面向科研）", "链路级为主", "无，多为参数面板", "有，指标由真实数据包涌现"],
    ["结果可验证", "需自建分析管线", "商业模型黑箱", "无理论对账", "每个实验自带理论答案，误差量化"],
    ["成本", "免费但人力成本高", "授权昂贵", "按项目采购数万~数十万元", "核心开源免费，服务订阅制"],
    ["二次开发", "门槛高（C++）", "封闭", "依赖厂商", "MIT 开源，Python/JS 生态，学生可改"]
  ]
}));
children.push(body("综上，星桥并非在某个单项上略胜，而是唯一同时满足“轻量部署、实时交互、包级真实、理论可验证、开源可改”五个教学刚需的方案。"));

// ===== 第四章 核心技术与创新 =====
children.push(h1("第四章  核心技术与创新点"));
children.push(h2("4.1  创新点一：让包级仿真“轻”下来"));
children.push(body("包级仿真是网络行为真实性的来源，但传统实现（ns-3 为代表）是面向科研的重型 C++ 框架。团队反其道而行：以 Python 标准库 heapq 事件队列 + 每端口 FIFO 存储转发 + 反向 Dijkstra 逐跳路由，用约 500 行核心代码实现了完整的包级仿真内核。轻不等于慢——长时压测实测引擎吞吐 1160 ticks/s；千星级（1584 星）实时仿真约 34 ticks/s，超出实时需求（5 ticks/s）近 7 倍。教学价值在于：这份代码量恰好落在“本科生一学期能读懂、能修改”的区间，平台本身就是一份可教学的高质量工程范例。"));
children.push(h2("4.2  创新点二：让动态网络“看得见、摸得着”"));
children.push(body("三层解耦架构配合墙钟插值渲染，把 5 Hz 的离散状态流平滑为 60 帧连续运动（实测浏览器帧率 120 FPS，1584 星下降至 48 FPS，均远超教学可用阈值）。学生可以暂停一个正在拥塞的网络，点开任意链路看队列深度曲线；可以 10 倍速快进观察星座运行一整天的切换规律。协议 v3.2 通过增量链路传输与位置压缩，把千星级稳态单帧控制在 50 KB 以内——这意味着一台普通教学服务器即可支撑整班并发访问，轻量化落到了带宽成本上。"));
children.push(h2("4.3  创新点三：给每个实验配上“标准答案”（对账体系）"));
children.push(body("这是星桥对虚拟仿真实验教学最重要的贡献。传统实验报告的问题是“无法判断对错”；星桥把仿真器本身做成可验证的对象，建立三级对账体系："));
children.push(bullet("守恒级——任意场景下“生成 = 送达 + 丢弃 + 在途”精确成立，杜绝暗箱；", "① "));
children.push(bullet("解析级——端到端时延与几何传播理论值精确吻合、逐跳分解误差 ≤ 0.075 ms、吞吐与理论负载误差 ≤ 4.2%；", "② "));
children.push(bullet("模型级——拥塞场景与 M/D/1 排队论理论值误差 1.7%，排队时延是从仿真中涌现的，而非反向拟合。", "③ "));
children.push(body("对教师而言，实验第一次变得可评分；对学生而言，“先预测、再运行、后对账”构成了完整的科学方法训练。固定随机种子保证全部结果可复现——这也是本项目区别于“PPT 项目”的底气：每一个数字都有测试脚本背书。"));
children.push(h2("4.4  创新点四：真实机制而非表面动画"));
children.push(body("平台上的每一个“现象”都由真实机制产生：切换丢包来自链路断开瞬间在队包与在途包的逐包清算；QoS 差异来自多级优先队列的严格优先调度与“低优先先丢”的缓冲管理；文件完整性来自 ARQ 重传与 SHA-256 校验。反面案例是多数演示型教学软件用脚本播放“看起来像”的动画——学生看完记住的是画面，不是机制。星桥的动画只是把已经发生的真实仿真事件画出来。"));
children.push(h2("4.5  技术指标实测汇总"));
children.push(cap("表 4-1  关键技术指标（全部实测，脚本随项目开源）"));
children.push(makeTable({
  headers: ["类别", "指标", "实测值"],
  widths: [1800, 3600, 3626],
  rows: [
    ["规模", "1584 星（三壳层 Walker 星座）实时仿真", "约 34 ticks/s（实时需求 5 ticks/s）"],
    ["规模", "1584 星浏览器三维渲染", "48 FPS（1080p，1617 个节点）"],
    ["规模", "稳态单帧 WebSocket 负载", "44.5~47.2 KB"],
    ["性能", "DES 引擎长时压测吞吐", "1160 ticks/s（1 小时无退化、无内存泄漏）"],
    ["性能", "全管线 300 秒仿真", "10,505,465 包送达、零丢包、19 ticks/s"],
    ["正确性", "M/D/1 排队论对账（ρ=0.8）", "实测 23.60 ms vs 理论 24.00 ms，误差 1.7%"],
    ["正确性", "端到端时延对账（3 跳轻载）", "21.037 ms，与理论值精确吻合"],
    ["正确性", "逐跳时延分解误差", "≤ 0.075 ms，端到端逐跳精确可加"],
    ["正确性", "包守恒不变量", "生成 = 送达 + 丢弃 + 在途，全部场景精确成立"],
    ["正确性", "切换丢包尖峰", "201 包（200 在队 + 1 在途），与计数精确一致"],
    ["正确性", "文件传输完整性", "SHA-256 逐字节一致（含移动目的端、高丢包场景）"],
    ["可靠性", "断线重连", "前端/后端/核心三层自愈，5 项专项测试通过"],
    ["质量", "自动化测试", "16+ 项专项测试 + 端到端测试，固定随机种子可复现"]
  ]
}));
children.push(h2("4.6  技术成熟度与知识产权"));
children.push(body("项目处于“完整可用的产品原型”阶段（TRL 6~7）：核心功能全部实现并通过测试，已在团队内部完成多轮全流程演示。知识产权方面：自研仿真引擎、后端与前端均为原创代码，采用 MIT 协议开源；项目基于同样 MIT 协议的 Hypatia 框架增强开发并遵守其许可；主链路不使用任何 GPL 组件，商业授权路径清晰。团队已启动软件著作权申请（平台软件 + 仿真引擎两件），并计划以教学论文形式在《实验技术与管理》《实验科学与技术》等实验教改期刊发表成果。"));

// ===== 第五章 市场分析 =====
children.push(h1("第五章  市场分析"));
children.push(h2("5.1  市场规模测算"));
children.push(body("星桥所处的细分市场是“高校虚拟仿真实验教学软件”，采用自下而上测算（假设明示于附注，供评审检验）："));
children.push(cap("表 5-1  TAM / SAM / SOM 测算（教育侧）"));
children.push(makeTable({
  headers: ["层级", "范围", "测算逻辑", "规模估算"],
  widths: [1200, 2600, 3200, 2026],
  rows: [
    ["TAM", "全国高校虚拟仿真实验软件市场", "约 1300 所本科高校 + 职业院校航天/通信专业群 + 科普场馆；按目标专业年均实验软件建设投入 20~80 万元测算", "约 1.2~4.8 亿元 / 年"],
    ["SAM", "通信 / 航空航天专业虚拟仿真细分", "开设通信工程或航空航天类本科专业的高校约 600+ 所，叠加一流课程建设专项", "约 0.6~1.9 亿元 / 年"],
    ["SOM", "三年可获取", "以本校为起点、深圳及周边高校先行，三年内累计试用/付费高校 10~20 所、课程共建 1~3 项", "约 20~150 万元（三年累计，用于验证单位经济而非追求规模）"]
  ]
}));
children.push(body("此外存在两类增量市场：商业航天企业培训（2025 年起行业新增企业快速攀升，新员工系统性培训需求刚显）与航天科普（全国科技馆/青少年宫体系），二者不作为近期收入主体，但为品牌与获客渠道。"));
children.push(h2("5.2  竞争格局"));
children.push(cap("表 5-2  竞争者分析"));
children.push(makeTable({
  headers: ["竞争者类型", "代表", "优势", "软肋（星桥的机会）"],
  widths: [2000, 1900, 2500, 2626],
  rows: [
    ["科研开源框架", "ns-3、Hypatia、OMNeT++", "学术公信力强、模型精细", "面向科研而非教学：无实时可视化、部署重、无课程化封装"],
    ["商业仿真软件", "STK、BTS 等行业工具", "工程精度高、行业标配", "授权昂贵、教学场景受限、网络级实时交互弱"],
    ["虚拟仿真厂商", "高校定制开发项目（实验空间平台在架项目）", "对接课程申报经验丰富", "多为单星链路级/演示动画型；按项目制交付、迭代慢、价格高；缺包级真实性"],
    ["潜在进入者", "大厂教育业务、出版社数字化部门", "渠道与资金雄厚", "缺乏卫星网络垂直 know-how 与经对账的仿真内核；教育细分市场对大厂吸引力有限"]
  ]
}));
children.push(h2("5.3  差异化定位"));
children.push(body("星桥的护城河由三层构成：第一层是“经理论对账的包级仿真内核”——对账体系需要排队论与网络工程的交叉能力，且必须逐项实测，难以速成；第二层是“实验内容与答案库”——六大实验的参数、观测点与评分标准持续积累，形成内容壁垒；第三层是“开源社区”——核心开源使 later-comers 即使复制代码，也难以复制社区教师共同维护的实验生态。定位一句话：不做最精确的仿真器，做最可信的教学平台。"));
children.push(h2("5.4  市场窗口"));
children.push(body("当前存在三重有利窗口：其一，千帆、GW 星座 2025 年起密集组网，“卫星网络”从前沿话题变为产业常识，课程需求逐步显现；其二，国家级一流课程“双万计划”与教育数字化战略推动高校持续投入虚拟仿真资源；其三，现有供给（科研工具与定制项目）结构性错位，尚无占据心智的教学品牌。窗口预计 2~3 年，率先完成课程共建、进入教师课堂的团队，更容易形成使用惯性与信任。"));

// ===== 第六章 商业模式 =====
children.push(h1("第六章  商业模式"));
children.push(h2("6.1  价值主张"));
children.push(box("对教师", "零门槛开出有标准答案的卫星网络实验课，直接服务于一流课程申报与教学成果积累。"));
children.push(spacer(40));
children.push(box("对学校", "以一张教学服务器级的成本，获得可私有化部署、可持续迭代的开放实验平台，摆脱按项目定制的高价低频采购。"));
children.push(spacer(40));
children.push(box("对学生与产业", "在校园里获得贴近商业航天岗位的网络系统认知训练；核心开源，优秀学生的贡献可被看见。"));
children.push(h2("6.2  收入模式：开源引流 + 四条收入线"));
children.push(cap("表 6-1  收入结构设计"));
children.push(makeTable({
  headers: ["收入线", "对象", "形态", "定价思路（规划）"],
  widths: [1750, 1900, 2900, 2476],
  rows: [
    ["开源社区版", "个人学习者、科创团队", "GitHub 开源，完整核心功能", "免费（获客与口碑主阵地）"],
    ["高校订阅版", "高校院系", "私有化部署 + 班级管理 + 实验库 + 自动报告 + 升级支持", "试点期 1~3 万元/校/年；正式版按规模与功能分级定价"],
    ["课程共建", "一流课程建设团队", "联合定制实验与申报材料，共享课程成果", "按项目 3~10 万元/项（试点期）"],
    ["企业培训版", "商业航天企业、院所", "定制场景（自有星座构型）+ 集训服务", "按项目报价，试点期 3 万元起"]
  ]
}));
children.push(body("开源与商业的关系遵循“核心开放、服务收费”：仿真内核、可视化与基础实验永久开源以扩大生态；班级管理、实验自动评分、报告导出、私有星座定制与 SLA 支持构成商业版本。此模式已在开发者工具与教育信息化领域被反复验证，且与团队“教育普惠”的价值观自洽。"));
children.push(h2("6.3  客户获取与服务"));
children.push(bullet("以赛促销路：中国国际大学生创新大赛、挑战杯、虚拟仿真实验教学项目评审等场合本身就是全国高校教师的聚集场；", "学术与竞赛场景获客："));
children.push(bullet("与 3~5 位种子教师共同打磨实验，以其课程为样板间，形成“同行的推荐最有效”的教研圈子传播；", "种子教师共创："));
children.push(bullet("依托哈工大（深圳）及深圳本地商业航天产业带，联动紫丁香学生卫星等真实工程场景形成背书；", "在地产业协同："));
children.push(bullet("开源仓库 + 实验指导书 + 教学视频公开，长尾流量沉淀为社区。", "内容营销："));

// ===== 第七章 营销与运营 =====
children.push(h1("第七章  营销与运营策略"));
children.push(h2("7.1  三步走推广路径"));
children.push(cap("表 7-1  推广阶段规划"));
children.push(makeTable({
  headers: ["阶段", "时间", "目标", "关键动作"],
  widths: [1500, 1300, 2600, 3626],
  rows: [
    ["本校验证", "2026.9 ~ 2027.6", "在本校 1~2 门课程真实授课，形成教学效果数据", "与通信类课程教师共建首个实验包；收集课前课后测评数据；产出教改论文与软著"],
    ["区域试点", "2027.7 ~ 2028.6", "深圳及周边 3~6 所高校试用（其中付费合作 1~3 所）", "以样板课程切入；参加教学学术会议；开源社区初步形成（数百 star、Issue/PR 活跃）"],
    ["稳定运营", "2028.7 起", "付费学校稳定续费，视试点数据决定是否扩大推广", "打磨产品化版本（含 SaaS 试点）；实验库开放投稿；申报虚拟仿真教学项目；不预设规模化节奏"]
  ]
}));
children.push(h2("7.2  运营重心"));
children.push(bullet("实验内容持续供给：每学期新增 1~2 个实验（如路由算法对比、雨衰影响、实时星历展示模式），保持课程新鲜度；"));
children.push(bullet("社区与生态：开源仓库 Issue/PR 治理、教师共创群、学生贡献者计划（优秀实验设计署名进入官方实验库）；"));
children.push(bullet("质量底线：全部对外版本必须通过可复现测试套件，保持“经对账”的品牌承诺不稀释。"));

// ===== 第八章 发展规划 =====
children.push(h1("第八章  发展规划"));
children.push(h2("8.1  技术路线"));
children.push(cap("表 8-1  技术演进路线"));
children.push(makeTable({
  headers: ["阶段", "技术目标", "说明"],
  widths: [1600, 2900, 4526],
  rows: [
    ["近期（0~12 个月）", "教学产品化", "一键启动安装包、免配置离线地球渲染、教师端班级管理与自动评分、实验指导书成套；接入 SGP4 真实轨道模型（TLE 星历），支持“真实 Starlink 星历”教学场景"],
    ["中期（12~24 个月）", "课程体系化", "对接《计算机网络》《卫星通信》课程大纲的实验矩阵；gym 接口封装支持强化学习创新实验；实验数据集与评分标准开放"],
    ["远期（24~36 个月）", "实时星历接入（方向性）", "接入 TLE 实时星历，使仿真星座与真实星座同步运动；“数字孪生”式教学场景与多校联合实验作为探索方向，视资源与需求决定投入"]
  ]
}));
children.push(h2("8.2  里程碑"));
children.push(cap("表 8-2  关键里程碑"));
children.push(makeTable({
  headers: ["时间", "里程碑", "验收标准"],
  widths: [1800, 3300, 3926],
  rows: [
    ["2026 Q4", "竞赛周期收官 + 软著 2 项受理", "大赛省赛及以上奖项；软著进入受理流程"],
    ["2027 Q2", "本校课程落地 1~2 门", "真实授课 ≥ 100 学生人次；课前课后测评形成对比数据"],
    ["2027 Q4", "试用高校 3~6 所 + 教改论文 1 篇", "其中付费合作 1~3 所；论文投稿实验类期刊"],
    ["2028 Q4", "付费学校 5~10 所，现金成本自给", "年度收入覆盖服务器、内容与知识产权现金成本（约 10 万元）"],
    ["2029 起", "产品化版本与社区稳定运营", "续费率过半；开源社区实验投稿常态化；视数据决定是否扩大投入"]
  ]
}));

// ===== 第九章 财务分析 =====
children.push(h1("第九章  财务分析（规划）"));
children.push(body("创意组阶段以“验证单位经济模型”为目标，财务数据均为基于明示假设的规划值。"));
children.push(h2("9.1  启动资金与用途"));
children.push(cap("表 9-1  启动资金规划（总额约 8 万元）"));
children.push(makeTable({
  headers: ["用途", "金额（万元）", "说明"],
  widths: [2200, 1500, 5326],
  rows: [
    ["云资源与带宽", "2.0", "教学服务器 2 台（8 核 32G）+ 对象存储 + 备份带宽，支撑试点期并发"],
    ["知识产权", "1.5", "软件著作权 2 项、必要的商标注册"],
    ["内容制作", "2.5", "实验指导书排版、教学视频录制、教材级图表"],
    ["市场与差旅", "1.5", "教学会议参展、种子院校拜访"],
    ["机动", "0.5", "风险预留"]
  ]
}));
children.push(body("资金来源：学校大创项目经费 + 竞赛奖金 + 学校创新创业孵化支持；暂不引入外部融资，优先以最小成本验证付费意愿。"));
children.push(h2("9.2  成本结构与单位经济"));
children.push(body("平台边际成本低是商业模式的基础：单实例状态流约 200 KB/s/学生（5 Hz × <50 KB 帧压缩后典型值），一台 8 核教学服务器即可支撑 1~2 个教学班并发；软件交付无实体成本，主要成本为研发人力（在校团队 + 勤工助学）与内容制作。按试点订阅价 1~3 万元/校/年、单校支撑成本 < 0.3 万元/年估算，单位经济为正——这是本项目最需要验证的假设。"));
children.push(h2("9.3  收入预测与成本覆盖"));
children.push(cap("表 9-2  三年收入预测（单位：万元，中性 / 保守两档）"));
children.push(makeTable({
  headers: ["年度", "订阅收入", "课程共建", "企业培训", "合计（中性 / 保守）"],
  widths: [1400, 1700, 1700, 1700, 2526],
  align: [AlignmentType.CENTER, AlignmentType.CENTER, AlignmentType.CENTER, AlignmentType.CENTER, AlignmentType.CENTER],
  rows: [
    ["2027", "2~9（1~3 校）", "5~10（1 项）", "0~5", "7~24 / 3~12"],
    ["2028", "8~24（4~8 校）", "10~25（1~2 项）", "2~8", "20~57 / 8~25"],
    ["2029", "15~45（8~15 校）", "10~30", "5~12", "30~87 / 12~40"]
  ]
}));
children.push(body("财务目标以“覆盖现金成本”为限：服务器、带宽、知识产权与内容制作的年度现金成本约 5~10 万元，团队人力主要来自在校投入与勤工助学，不计薪。按中性情景，2028 年可覆盖全部现金成本；保守情景下落在 2029 年。是否加大投入，以本校与试点校的教学效果数据、付费续费率为准——这正是创意组阶段最需要验证的假设。敏感性主要来自高校采购周期（6~12 个月），对策是以共建项目（预算走课程建设经费、周期短）先行，订阅制随后跟进。"));

// ===== 第十章 团队 =====
children.push(h1("第十章  团队介绍"));
children.push(h2("10.1  团队构成"));
children.push(cap("表 10-1  团队成员与分工（可按最新名单更新）"));
children.push(makeTable({
  headers: ["成员", "角色", "主要贡献"],
  widths: [1600, 2000, 5426],
  rows: [
    ["李思达", "负责人 / 仿真引擎与总体架构", "自研 DES 仿真引擎；千星级扩展与协议设计；对账测试体系；项目路线规划"],
    ["符浩原", "仿真与测试工程", "多域流与 QoS 机制；自动化测试与压测；实验参数设计"],
    ["李璟哲", "前端与可视化", "CesiumJS 三维前端；插值渲染与时序图表；交互面板"],
    ["焦健 教授", "指导教师（信息科学与技术学院）", "研究方向指导；教学场景把关；课程资源对接"]
  ]
}));
children.push(body("团队三人自大一入学即共同投入本项目，全部代码与文档均可在版本库中追溯署名，项目与团队关系的真实性可查证。团队已按“仿真引擎 / 测试验证 / 前端交互”形成稳定分工与每周例会机制，并在七阶段研发中经受了长周期协作的检验。"));
children.push(h2("10.2  院校支撑与人才培养（个人成长维度）"));
children.push(body("本项目是哈工大（深圳）大一年度项目的直接成果：学校提供立项经费、实验室与中期检查机制，使三名大一新生得以在入学第一年进入真实科研训练。项目全程体现了“新工科”的建设导向——以真实产业问题（卫星网络教学工具缺位）为牵引，融合计算机网络、轨道力学、排队论、软件工程多学科知识，完成从选题调研、方案论证、工程实现到实测验证的完整闭环。团队成员在一年内从编程入门者成长为能独立设计仿真引擎的工程者，本身就是创新教育成效的最好证明。项目还得到了信息科学与技术学院焦健教授的持续指导，形成了“大一项目—教授课题组—竞赛孵化”的科教融汇链条。"));
children.push(h2("10.3  团队发展规划"));
children.push(bullet("补强市场与教育方向成员（经管/教育专业 1~2 人），负责课程共建洽谈与用户调研；"));
children.push(bullet("邀请产业导师（商业航天企业工程师）加入顾问团，强化企业培训线的专业背书；"));
children.push(bullet("建立梯队：每学年在指导教师课题组内招募 2~3 名低年级成员，保障项目延续性。"));

// ===== 第十一章 社会价值 =====
children.push(h1("第十一章  社会价值与教育价值"));
children.push(h2("11.1  服务国家战略：卫星互联网人才自主培养"));
children.push(body("卫星互联网是国家新型基础设施与太空经济竞争的核心赛道，47.5 万人的航空航天人才缺口需要高校规模化供给。星桥把“星座组网”这一原本依赖昂贵设备的实验内容，压缩到一台笔记本电脑即可完成，直接扩大了相关课程的供给能力——让没有卫星、没有机房预算的院校，也能开出高质量卫星网络实验课。"));
children.push(h2("11.2  教育公平：优质实验资源的普惠分发"));
children.push(body("核心开源 + 低硬件门槛的组合，使中西部院校、职业院校与资源受限的中学社团可以零成本获得与世界一流工具同源的实验环境。这既是技术普惠，也是对“虚拟仿真实验资源分布不均”这一结构性问题的直接回应。"));
children.push(h2("11.3  航天科普与科学家精神培育"));
children.push(body("三维地球上千颗卫星实时组网的画面天然具备科普感染力。平台可为科技馆、航天日主题活动提供离线展项，让青少年“亲手让一颗卫星上网”，在互动中理解系统工程之美，厚植航天报国情怀。"));
children.push(h2("11.4  就业与产业带动"));
children.push(body("近中期，项目通过课程共建与订阅服务带动在校研发、内容制作等岗位；若商业化验证顺利，可依托指导教师课题组与学校孵化机制形成小型维护团队。更重要的是间接带动：每一所接入星桥的院校，都在为商业航天产业输送具备网络系统思维的工程师。"));

// ===== 第十二章 风险 =====
children.push(h1("第十二章  风险分析与应对"));
children.push(cap("表 12-1  风险清单与应对"));
children.push(makeTable({
  headers: ["风险类别", "风险描述", "发生可能性 / 影响", "应对策略"],
  widths: [1500, 2900, 1500, 3126],
  rows: [
    ["技术风险", "真实轨道（SGP4/TLE）与信道模型尚未接入，评审或用户可能质疑真实性", "中 / 中", "已列入近期路线；当前圆轨道模型的时延对账精度反而更利于教学；接入后以真实 Starlink 星历形成新亮点"],
    ["市场风险", "高校采购周期长、预算碎片化", "高 / 中", "以课程共建经费（周期短）先行；开源免费版降低决策门槛；聚焦有课程建设任务的院校"],
    ["竞争风险", "虚拟仿真厂商或大厂进入细分", "中 / 中", "开源社区 + 经对账内核 + 实验内容库三层壁垒；保持每学期内容迭代速度"],
    ["团队风险", "学生团队时间与延续性约束", "中 / 高", "依托指导教师课题组建立梯队；关键模块文档化；核心代码 MIT 开源可被社区承接"],
    ["合规风险", "开源许可与数据合规", "低 / 低", "已完成许可梳理（MIT 路径，主链路无 GPL 组件）；TLE 等公开数据无合规障碍"],
    ["质量风险", "快速扩张导致“经对账”承诺稀释", "低 / 高", "全部对外版本强制通过可复现测试套件；建立发布检查单制度"]
  ]
}));

// ===== 附录 =====
children.push(h1("附  录"));
children.push(h2("附录 A  评审维度对照表"));
children.push(cap("表 A-1  大赛评审要点与本计划书对应章节"));
children.push(makeTable({
  headers: ["评审维度（分值）", "本计划书对应内容"],
  widths: [2600, 6426],
  rows: [
    ["个人成长（30）", "1.2 项目缘起（扎根实际选题）；10.2 院校支撑与人才培养（新工科、科教融汇）；第二章（调研深入）；全文数据可复现（求真务实）"],
    ["项目创新（30）", "第四章四大创新点；3.3 六大实验（服务创新）；第六章开源+订阅模式（商业模式创新）；4.6 成果与知识产权"],
    ["产业价值（25）", "第五章市场分析（产业认知与定位）；第六、七章商业模式与营销；第九章财务规划；第十一章社会影响"],
    ["团队协作（15）", "第十章团队构成、分工机制、院校与课题组资源"]
  ]
}));
children.push(h2("附录 B  数据来源说明"));
children.push(bullet("平台技术指标：全部来自项目实测记录与自动化测试（固定随机种子，脚本随开源仓库提供）；"));
children.push(bullet("航空航天人才缺口 47.5 万人：工业和信息化部《制造业人才发展规划指南》（转引自行业公开报道，2026）；"));
children.push(bullet("2025 年新增商业航天企业 83 家（同比 +277%）、现存 9.7 万家（截至 2026 年 3 月）：行业公开统计报道；"));
children.push(bullet("卫星互联网核心环节人才缺口 2.8 万人：2025 年卫星互联网产业人才分析报告（公开资料）；"));
children.push(bullet("虚拟仿真实验教学课程属国家级一流本科课程类别、实验空间平台在架卫星通信类项目：教育部公开文件与国家实验空间平台（ilab-x.com）；"));
children.push(bullet("市场规模测算假设：本科高校约 1300 所、开设通信/航空航天类专业高校约 600+ 所、目标专业年均实验软件投入 20~80 万元——均为可公开核验的量级估计，用于说明市场量级而非精确预测。"));
children.push(h2("附录 C  术语表"));
children.push(makeTable({
  headers: ["术语", "释义"],
  widths: [2200, 6826],
  rows: [
    ["LEO", "低地球轨道（Low Earth Orbit），通常指 300~2000 km 高度的卫星轨道"],
    ["DES", "离散事件仿真（Discrete Event Simulation），以事件队列驱动的仿真方法"],
    ["ISL / GSL / SUL / SSL", "星间链路 / 星地链路 / 无人机上行链路 / 船舶上行链路"],
    ["M/D/1", "经典排队模型：泊松到达、定长服务、单服务台"],
    ["ARQ", "自动重传请求（Automatic Repeat reQuest），可靠传输机制"],
    ["TLE / SGP4", "双行根数轨道数据格式 / 其对应的标准轨道外推模型"],
    ["SHA-256", "密码学哈希函数，此处用于文件传输完整性逐字节校验"],
    ["Walker 星座", "Walker 描述的多轨道面均匀星座构型，Starlink 等均属此类"]
  ]
}));
children.push(spacer(200));
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [t("—— 本计划书完 ——", { size: 18, color: GRAY })] }));

// ---------- document ----------
const doc = new Document({
  creator: "李思达",
  lastModifiedBy: "李思达",
  title: "星桥·卫星网络虚拟仿真实验平台项目计划书",
  subject: "中国国际大学生创新大赛 高教主赛道 本科生创意组",
  description: "哈尔滨工业大学（深圳）星桥项目组",
  keywords: "卫星网络;虚拟仿真;实验教学;星桥;StarBridge",
  styles: {
    default: {
      document: { run: { font: F, size: 21 } }
    },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, color: BLUE, font: F },
        paragraph: { spacing: { before: 240, after: 200 }, outlineLevel: 0, keepNext: false, keepLines: false } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, color: MIDBLUE, font: F },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 1, keepNext: false, keepLines: false } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, color: "404040", font: F },
        paragraph: { spacing: { before: 140, after: 80 }, outlineLevel: 2, keepNext: false, keepLines: false } }
    ]
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 620, hanging: 300 } } } }] },
      { reference: "numbers",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 620, hanging: 300 } } } }] }
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        alignment: AlignmentType.RIGHT,
        children: [t("星桥 · 卫星网络虚拟仿真实验平台 — 项目计划书", { size: 16, color: GRAY })] })] })
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [t("第 ", { size: 16, color: GRAY }),
                   new TextRun({ children: [PageNumber.CURRENT], font: F, size: 16, color: GRAY }),
                   t(" 页", { size: 16, color: GRAY })] })] })
    },
    children
  }]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("星桥·卫星网络虚拟仿真实验平台-项目计划书.docx", buffer);
  console.log("OK plan.docx bytes=", buffer.length);
});
