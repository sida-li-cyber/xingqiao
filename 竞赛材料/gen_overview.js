const fs = require("fs");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        Header, Footer, AlignmentType, BorderStyle, WidthType, ShadingType, PageNumber } = require("docx");

const F = { ascii: "Arial", hAnsi: "Arial", eastAsia: "Microsoft YaHei" };
const BLUE = "1F4E79", GRAY = "7F7F7F";
const CW = 9026;

const t = (text, opts = {}) => new TextRun({ text, font: F, ...opts });

const OVERVIEW_TEXT =
  "星桥·卫星网络虚拟仿真实验平台（StarBridge）是哈尔滨工业大学（深圳）本科生团队（李思达、符浩原、李璟哲，指导教师焦健教授）依托校级大一年度项目研发的轻量级卫星网络虚拟仿真教学平台，目标是把卫星网络实验从机房搬进浏览器。当前卫星互联网产业高速发展，工信部预测航空航天装备领域人才缺口达47.5万人，星座调度等“组网类”岗位尤为紧缺；但现有工具要么如ns-3般编译繁琐、学习曲线以月计，要么授权昂贵，且普遍存在“现象看不见、结果无答案”的问题，绝大多数院校开不出像样的卫星网络实验课。星桥采用“仿真核心—实时后端—浏览器前端”三层解耦架构：自研轻量级包级离散事件仿真引擎，让时延、丢包、链路利用率等指标由真实数据包“涌现”而非人工设定；FastAPI/WebSocket后端实时转发状态；CesiumJS前端在三维地球上呈现千颗卫星的实时运动、按指标着色的链路与沿链路流动的数据脉冲，支持播放、暂停、倍速与任意跳转，可切换Starlink、Kuiper、Telesat等星座与多种信道场景。平台内置时延分解与对账、拥塞与排队论、切换丢包、QoS优先级、可靠文件传输、多域组网设计六大教学实验，每个实验自带理论答案与观测点：M/D/1排队模型对账误差仅1.7%，包守恒不变量（生成＝送达＋丢弃＋在途）在全部场景精确成立，10MB以上文件经SHA-256逐字节校验一致，教师第一次可以像批改数学题一样批改网络实验。全部指标实测可复现：1584颗卫星实时仿真约34 ticks/s、浏览器48 FPS，300秒全管线1050万包零丢包，16项以上固定种子自动化测试全部通过；系统纯Python零编译、4GB内存即可运行，五分钟从安装到看见第一个数据包。核心引擎以MIT协议开源，面向高校教学、职业培训、航天科普与企业培训四类场景，构建“开源社区＋试点订阅＋课程共建＋企业培训”的轻量商业模式；发展路径为先本校课程验证、再区域小范围试点，商业化以覆盖成本、验证付费意愿为目标，小步迭代，服务卫星互联网国家战略下的人才培养。";

console.log("字数（含标点）＝", OVERVIEW_TEXT.length);

const children = [];

// header banner
children.push(new Table({
  width: { size: CW, type: WidthType.DXA }, columnWidths: [CW],
  rows: [new TableRow({ children: [new TableCell({
    borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" } },
    margins: { top: 140, bottom: 160, left: 200, right: 200 },
    width: { size: CW, type: WidthType.DXA },
    shading: { fill: BLUE, type: ShadingType.CLEAR },
    children: [
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
        children: [t("星桥 · 卫星网络虚拟仿真实验平台", { size: 34, bold: true, color: "FFFFFF" })] }),
      new Paragraph({ alignment: AlignmentType.CENTER,
        children: [t("项目介绍概述  |  StarBridge — Lightweight LEO Network Lab for Education", { size: 19, color: "D6E4F0" })] })
    ] })] })]
}));
children.push(new Paragraph({ children: [t(" ", { size: 8 })], spacing: { after: 160 } }));

// 概述正文：一整段
children.push(new Paragraph({
  children: [t(OVERVIEW_TEXT)],
  spacing: { line: 340, after: 80 },
  indent: { firstLine: 420 },
  alignment: AlignmentType.JUSTIFIED
}));

children.push(new Paragraph({ children: [t(" ", { size: 8 })], spacing: { after: 200 } }));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER, spacing: { after: 60 },
  children: [t("联系方式：李思达（团队负责人） · 哈尔滨工业大学（深圳）", { size: 19, color: GRAY })] }));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [t("项目仓库与技术验证报告随附（固定随机种子，全部指标可复现）", { size: 19, color: GRAY })] }));

const doc = new Document({
  creator: "李思达",
  lastModifiedBy: "李思达",
  title: "星桥·卫星网络虚拟仿真实验平台项目介绍概述",
  subject: "中国国际大学生创新大赛 高教主赛道 本科生创意组",
  description: "哈尔滨工业大学（深圳）星桥项目组",
  keywords: "卫星网络;虚拟仿真;实验教学;星桥;StarBridge",
  styles: {
    default: { document: { run: { font: F, size: 21 } } }
  },
  sections: [{
    properties: {
      page: { size: { width: 11906, height: 16838 },
        margin: { top: 1300, right: 1440, bottom: 1300, left: 1440 } }
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        alignment: AlignmentType.RIGHT,
        children: [t("星桥 · 卫星网络虚拟仿真实验平台 — 项目介绍概述", { size: 16, color: GRAY })] })] })
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
  fs.writeFileSync("星桥·卫星网络虚拟仿真实验平台-项目介绍概述.docx", buffer);
  console.log("OK overview.docx bytes=", buffer.length);
});
