# Test App Business Logic

## 1. Initialization

The Test App initializes the Daa SDK with the Daa backend API URL and an authentication token. These values are retrieved from the environment variables of the application.

## 2. Exception Handling

The Test App has an endpoint `/error` that raises an exception. The exception is caught in a try-except block. The Daa SDK is used in the except block to send the error log to the Daa backend API. The endpoint then returns a JSON response with a 500 status code.

## 3. Simulated Errors

The Test App includes several endpoints to simulate different types of Python runtime errors for testing purposes:

- **`/attribute-error`**: Triggers an `AttributeError`.
- **`/import-error`**: Triggers an `ImportError`.
- **`/index-error`**: Triggers an `IndexError`.
- **`/name-error`**: Triggers a `NameError`.
- **`/recursion-error`**: Triggers a `RecursionError`.
