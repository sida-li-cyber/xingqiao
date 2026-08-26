/**
 * 共享常量：链路 / 节点类型元数据（标签与颜色）。
 *
 * cesium-manager.js（3D 实体着色）与 ui-controller.js（详情面板徽章）
 * 都以本文件为唯一事实来源，避免多处硬编码漂移。
 * index.html 图层面板的色块为静态样式，数值需与本表保持一致。
 *
 * 必须在 cesium-manager.js / ui-controller.js 之前加载。
 */
(function () {
    'use strict';

    window.SBConstants = {
        /** 链路类型（协议 v2 定义）：isl 星间 / gsl 地面 / sul 无人机 / ssl 船舶 */
        LINK_TYPES: {
            isl: { label: 'ISL 星间链路', color: '#4FC3F7' },
            gsl: { label: 'GSL 地面-卫星', color: '#FF8A65' },
            sul: { label: 'SUL 卫星-无人机', color: '#81C784' },
            ssl: { label: 'SSL 卫星-船舶', color: '#FFB74D' },
        },
        /** 节点类型：颜色对应 Cesium 命名色 DODGERBLUE/LIMEGREEN/ORANGE/ORANGERED */
        NODE_TYPES: {
            satellite:      { label: '卫星',   color: '#1E90FF' },
            uav:            { label: '无人机', color: '#32CD32' },
            ship:           { label: '船舶',   color: '#FFA500' },
            ground_station: { label: '地面站', color: '#FF4500' },
        },
    };
})();
