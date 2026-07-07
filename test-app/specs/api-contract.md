# API Contract

## GET /error

- **Method**: GET
- **Description**: Triggers a simulated internal server error to test exception handling.
- **Responses**:
  - **500 Internal Server Error**:
    - **Content-Type**: `application/json`
    - **Body**:
      ```json
      {
        "error": "Internal Server Error"
      }
      ```

## GET /attribute-error

- **Method**: GET
- **Description**: Triggers an `AttributeError` by calling a non-existent method on an object.
- **Responses**:
  - **500 Internal Server Error**:
    - **Content-Type**: `application/json`
    - **Body**:
      ```json
      {
        "error": "Internal Server Error"
      }
      ```

## GET /import-error

- **Method**: GET
- **Description**: Triggers an `ImportError` by trying to import a non-existent module.
- **Responses**:
  - **500 Internal Server Error**:
    - **Content-Type**: `application/json`
    - **Body**:
      ```json
      {
        "error": "Internal Server Error"
      }
      ```

## GET /index-error

- **Method**: GET
- **Description**: Triggers an `IndexError` by accessing a non-existent index in a list.
- **Responses**:
  - **500 Internal Server Error**:
    - **Content-Type**: `application/json`
    - **Body**:
      ```json
      {
        "error": "Internal Server Error"
      }
      ```

## GET /name-error

- **Method**: GET
- **Description**: Triggers a `NameError` by accessing a non-existent variable.
- **Responses**:
  - **500 Internal Server Error**:
    - **Content-Type**: `application/json`
    - **Body**:
      ```json
      {
        "error": "Internal Server Error"
      }
      ```

## GET /recursion-error

- **Method**: GET
- **Description**: Triggers a `RecursionError` by calling a function that calls itself indefinitely.
- **Responses**:
  - **500 Internal Server Error**:
    - **Content-Type**: `application/json`
    - **Body**:
      ```json
      {
        "error": "Internal Server Error"
      }
      ```
