# 空路通桌面演示输入

park.json 是原 park_crane_synthetic.json 的未改参数副本；motion.json 是原 motion_crane_synthetic.json 的未改参数副本。复制用于独立前端演示目录，不代表新增独立研究样本。
completion.json 是明确标注的合成完成记录：有效/到达均为 t+360 s，必须先将 end_unconfirmed 场景推进至360，再导入校验、明确接受。该记录结束活动，不确认设施拆除；不读取仿真隐藏真值。

所有坐标均为虚构局部 ENU 米，地面 z=0，无现实地点。显示锚点在 configs/portal-demo.json，仅用于3D渲染。
