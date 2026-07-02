"""Shared exception definitions for NYX system."""

class NYXException(Exception):
    """Base exception class for all custom NYX exceptions.

    This exception class serves as the root of the NYX error hierarchy. All
    custom exceptions within the NYX system should inherit from NYXException
    to ensure consistent error handling and categorization.

    When designing new exception types, follow this pattern:
        class MyCustomError(NYXException):
            '''Specific description of when this error occurs.'''
            pass

    This allows for easy catching of all NYX-related exceptions while maintaining
    a clear hierarchy for debugging and logging purposes.
    
    Supports exception chaining and descriptive messages for better debugging.
    """
    def __init__(self, message=None, cause=None):
        super().__init__(message)
        self.cause = cause
        if cause is not None:
            # Chain the exceptions properly
            self.__cause__ = cause

    def __str__(self):
        """Provide a descriptive string representation including the cause when present."""
        if self.cause:
            return f"{super().__str__()} (caused by: {self.cause})"
        return super().__str__()

    def __repr__(self):
        """Provide an unambiguous string representation for debugging and logging."""
        message = str(self)
        if self.cause:
            return f"{self.__class__.__name__}(message={message!r}, cause={self.cause!r})"
        return f"{self.__class__.__name__}(message={message!r})"

    def __reduce__(self):
        """Enable serialization for pickling by providing reduction tuple."""
        # Store the original message and cause separately to avoid double formatting
        return (self.__class__, (super().__str__(), self.cause))

class TimeoutError(NYXException):
    """Custom timeout exception used across the system."""
    pass


class UpgradeFailed(NYXException):
    """Raised when a code evolution step fails."""
    pass