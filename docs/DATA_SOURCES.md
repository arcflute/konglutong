# 数据来源与许可记录

本项目的园区、塔吊、施工时间、任务、运动参数和训练事件仍为合成示例。位置或参数看似具体不构成真实测量，来源标签必须保留。不同来源即使将来组合演示也只能标为mixed_simulation，不能宣称为真实同一地点的事件链。

## round2与round3合成材料

- `examples/park_crane_synthetic.json`及运动/协同演示：项目合成，无现实地点和设备运行事实声明。
- `examples/imports/construction_delay.json/.csv`：延期字段记录，来源structured_demo，保持原活动开始和未知完成状态。
- `examples/imports/park_tasks_resources.json`：园区建筑、设施、起降点、新任务/服务和等待资源的合成追加模板。
- `examples/imports/surveyed_pad.geojson`及geojson_metadata：经纬度点及局部对应关系均为合成，验证明确坐标对应，不是实际测量控制点。
- `examples/imports/metadata.json`：明确局部米制、flat-ground-z0高度、轴和模拟时间原点，没有未经核对的投影转换。
- 学习数据分组、真值隔离与独立量以LEARNING和实验目录清单为准。round2旧产物保留，round3写独立目录。

## 唯一下载的公开格式样本

联网获取了USGS一个历史地震的GeoJSON记录，1062字节，仅用于证明**外部经纬度/深度材料不能直接作为园区米制施工证据**。它不是施工材料，不加入园区、不训练模型，不表示地震与示例园区存在关联。

- 来源：USGS Earthquake Hazards Program。精确查询URL、主机记录的获取UTC时间、SHA256及字节数在`examples/imports/public/manifest.json`；原始小文件为`usgs_one_event.geojson`。
- 许可核对：[USGS版权说明](https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted)指出USGS生产的数据/信息属美国公有领域；本次查询限定contributor=us，仅保存事实记录，没有复制照片或第三方插图。
- 格式与字段：[USGS GeoJSON说明](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)。其位置第三值为震源深度（公里），不能当作米制高度z。
- 用途：离线负例回归，实际得到unsupported（缺明确测量点映射），world_updated=false。
- 缺失：园区对应控制点、本地高度基准、施工实体及字段来源、模拟时间原点、施工状态。没有生成这些缺失字段来让导入通过。

未找到并验证适合本园区吊装任务、同时具有几何/基准/时间/使用许可的公开施工材料。因此本轮没有声称完成真实施工提取效果验证；后续需要用户提供可使用的设备图纸、施工记录和对应坐标基准，或给出具体授权数据源。


## 第四轮来源与血缘

`examples/round4/dynamic_window.json`为自行构造的可手算局部米制园区，record_initial及零误差均为显式合成假设，无真实地理映射。本轮未下载新公共材料、未调用外部多模态；原结构化样例和已有公开负例来源保留。

初版round4生成器由同一基础拓扑作参数扰动，不能把24个随机seed当24个独立基础模板；其协议/结果保留但只作诊断。纠正后的独立手工拓扑与全部变体血缘、到达序列和冻结清单在round4独立数据目录，最终统计见ROUND4_RESULTS。仿真器持有未来事件，在线world仅接收当时已到达的记录；模型特征不含未来真值、参考解和求解后代价。所有组合场景均为合成仿真，无真实同一地点事件的含义。
