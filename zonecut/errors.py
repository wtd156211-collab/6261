"""zonecut 的统一错误类型。"""


class ZonecutError(Exception):
    """输入不可用：文件缺失、格式不合法、id 违反约束、条件形式不支持等。

    CLI 捕获后以退出码 1 结束，且不写任何输出文件。
    """
