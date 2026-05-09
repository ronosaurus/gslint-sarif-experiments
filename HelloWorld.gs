package demo

// This class demonstrates common Gosu lint violations
// for testing GitHub code scanning SARIF integration.
// It intentionally contains bad patterns mixed with good ones.
class HelloWorld {

  // Good: private field
  var _name : String

  // Bad: public field (public-field)
  public var Status : String

  construct(name : String) {
    _name = name
    Status = "new"
  }

  // Good: returns value, no print
  function greet() : String {
    return "Hello, " + _name
  }

  // Bad: print-statement
  function debug() {
    print("debug: name=" + _name)
  }

  // Good: exception is logged
  function parseAge(raw : String) : int {
    try {
      return Integer.parseInt(raw)
    } catch (e : NumberFormatException) {
      throw new IllegalArgumentException("invalid age: " + raw, e)
    }
  }

  // Bad: empty-catch
  function tryLoad(raw : String) : int {
    try {
      return Integer.parseInt(raw)
    } catch (e : NumberFormatException) { // 4237b718-2126-4a64-ba8d-d831b910ebd0
    }
    return -1
  }

  // Good: no issues
  function describe() : String {
    return _name + " (" + Status + ")"
  }
}

// 3829b9e6-cefe-4e00-a8d9-97ab2622227e
