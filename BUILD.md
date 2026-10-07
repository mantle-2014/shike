# 从源码构建（Windows）

本仓库只保存当前版本的源码、程序图标和构建配置。发布给普通用户的是构建后的 `Shike.exe`；不要把 `.venv`、`build`、`dist`、用户数据库或日志提交进源码仓库。

## 环境

- Windows 10/11，64 位。
- Python 3.10 或更新的 64 位版本，带 `pip` 和 `venv`。
- 构建时需要获取 `requirements-build.txt` 中的依赖；打包后的程序运行时不需要联网或安装 Python。

建议在**干净的虚拟环境**中构建，避免其他软件的 Qt/ICU DLL 混入。原开发机曾出现外部 ICU DLL 与 Qt 冲突，因此当前打包配置不引用本机绝对路径或旧环境的 `pydeps`。

## 构建命令

在仓库根目录打开 PowerShell，执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1
```

产物位于 `dist\Shike.exe`。可直接双击运行；首次启动时会在 `%LOCALAPPDATA%\TaskTimeDesk` 建立本地数据文件。向用户分发前，建议在另一台未安装 Python 的 Windows 电脑上进行启动、计时、便签保存和退出后重开检查。

## 本地源码运行

安装依赖后可以运行：

```powershell
.\.venv\Scripts\python.exe .\src\qt_app.py
```

源码入口是 `src\qt_app.py`；它导入同目录的 `store.py` 和 `qt_widgets.py`。程序以当前用户身份访问本地数据库。开发时若不想接触已有数据，可在启动前设置 `TASKTIME_CONFIG_DIR` 和 `TASKTIME_DATA_DIR` 指向专门的临时目录。

## 发布检查

1. 检查源码和构建依赖版本已确定。
2. 运行 `python -m py_compile src\qt_app.py src\qt_widgets.py src\store.py` 做语法检查。
3. 在干净环境打包，并手动验证 EXE 的主要功能。
4. 将 `dist\Shike.exe` 作为 GitHub Release 附件上传；仓库保留源码，不跟踪二进制产物。

本仓库尚未附带开源许可证。若计划公开发布并允许他人修改、再分发，请先选择并加入合适的 `LICENSE` 文件。
