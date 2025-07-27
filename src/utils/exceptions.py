"""
自定义异常类
"""


class APIConverterException(Exception):
    """API转换器基础异常"""
    pass


class ConfigurationError(APIConverterException):
    """配置错误"""
    pass


class ChannelError(APIConverterException):
    """渠道错误"""
    pass


class CapabilityDetectionError(APIConverterException):
    """能力检测错误"""
    pass


class ConversionError(APIConverterException):
    """转换错误"""
    pass


class AuthenticationError(APIConverterException):
    """认证错误"""
    pass


class RateLimitError(APIConverterException):
    """速率限制错误"""
    pass


class NetworkError(APIConverterException):
    """网络错误"""
    pass


class ChannelNotFoundError(APIConverterException):
    """渠道未找到错误"""
    pass


class APIError(APIConverterException):
    """API错误"""
    pass


class ValidationError(APIConverterException):
    """验证错误"""
    pass


class TimeoutError(APIConverterException):
    """超时错误"""
    pass