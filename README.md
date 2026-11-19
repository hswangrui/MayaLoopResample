# Loop Resample for Maya

把 Blender 插件 [Loop Resample](https://github.com/YLIANG021/Loop-Resample) 的边环重采样能力移植到 Maya 2019 / 2020。

在视口中选中一段连续边链，用滚轮直接增减段数；支持平滑、均匀分布、UV / 材质 / 硬边保留，并可一次撤销。

> 本项目是独立移植，不是 YLIANG 的官方 Maya 版本。原插件为 GPL-3.0-or-later，本移植沿用同一许可证。

## 环境

- Autodesk Maya 2019 或 2020
- Maya 自带的 Python 2.7、Maya API 2.0、PySide2
- 无第三方 Python 依赖，不需要编译

## 安装

1. 保存当前场景并重启 Maya。
2. 把仓库根目录的 `install.mel` 拖进 Maya 视口。
3. 选择本仓库所在目录（包含 `install.py`）。
4. 安装会创建 Maya 模块并启用本插件自动加载，不会新建 Shelf。

安装完成后，工具入口在：

**空格热盒 → 编辑网格（Edit Mesh）→ Loop Resample**

也可以在脚本编辑器中直接启动：

```python
import loop_resample_maya
loop_resample_maya.show()
```

## 使用

1. 选择网格上**一段连续边链**，或整圈边环。
2. 启动 Loop Resample。
3. 鼠标移回视口，直接调整：

| 操作 | 功能 |
| --- | --- |
| 滚轮 | 增减段数 |
| `Shift` + 滚轮 | 调整平滑 |
| `Ctrl` + 滚轮 | 调整均匀分布 |
| 左键 / `Enter` | 确认，可一次撤销 |
| 右键 / `Esc` | 取消并恢复原网格 |
| `R` | 重置参数 |
| `Alt` + 鼠标 | 保留 Maya 相机导航 |

视口左下角会显示当前段数和参数，提示框不拦截鼠标。

## 支持范围

- 开放边链、闭合边环
- 多条边链共同增减段数
- 单排三角面 / 四边面条带重建
- 三角扇和常见多边形封口
- 多 UV 集、UV 接缝、面材质、硬边保留
- 参数始终基于原网格计算，预览可反复切换
- 确认后整次操作只产生一个撤销步骤

## 不支持或会拒绝的情况

为避免静默破坏模型，以下输入会直接报错而不是猜测处理：

- 有构造历史、蒙皮、变形器的网格
- 实例、引用或锁定网格
- 网格仍驱动下游几何
- 颜色集、锁定法线、折痕权重、Mesh Blind Data、组件集
- 分叉或相交的边选择
- 带洞多边形
- 无法在保留 UV / 材质 / 硬边分界的前提下减到目标段数

复杂条带布局也可能被拒绝。建议先在保存过的副本上试用，确认结果后再提交到正式资产。

## 已知限制

- 以视口滚轮交互为主，不是 Blender 原版的完整模态工具；旧参数面板仅保留脚本入口
- 没有全局曲面碰撞检测
- 不插值自定义法线、蒙皮权重或顶点色
- 顶点 / 面 / UV 编号可能变化
- Maya 2019 / 2020 已通过自动化测试；更高版本未验证

## 测试

离线算法测试：

```bash
python -m unittest discover -s tests -p test_geometry.py -v
```

Maya 集成测试需要用对应版本的 `mayapy` 在独立场景中运行：

```bash
mayapy tests/maya_smoke.py
mayapy tests/viewport_smoke.py
mayapy tests/ui_smoke.py
```

GUI 输入、热盒和安装流程测试会在新启动的 Maya 进程中创建并丢弃测试场景，入口见 `tests/*_gui_smoke.py`。

## 致谢

- 原插件：[YLIANG021/Loop-Resample](https://github.com/YLIANG021/Loop-Resample)
- 采样、平滑和条带重建思路来自原项目
- Maya 适配、属性转移、视口交互、撤销命令、安装器和测试为本项目重写

许可证与来源说明见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
