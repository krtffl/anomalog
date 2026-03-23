//! PyO3 bridge to logmole-core for fast log parsing and Drain template extraction.

use std::collections::HashMap;

use logmole_core::analysis::DrainTree as RustDrainTree;
use logmole_core::record::{Format, Value};
use logmole_core::ParserRegistry;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Detect the log format from a sample of lines.
///
/// Returns the format name as a string (e.g., "json", "nginx", "syslog3164").
#[pyfunction]
fn detect_format(lines: Vec<String>) -> String {
    let refs: Vec<&str> = lines.iter().map(String::as_str).collect();
    logmole_core::detect_format(&refs).as_str().to_string()
}

/// Parse a single log line and return a Python dict, or None if unparsable.
///
/// The returned dict has keys: timestamp, level, message, fields, format, line_number.
#[pyfunction]
fn parse_line(py: Python<'_>, line: &str, format_hint: Option<&str>) -> PyResult<Option<PyObject>> {
    let registry = ParserRegistry::new();

    let format = match format_hint {
        Some(hint) => hint
            .parse::<Format>()
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?,
        None => {
            let sample = [line];
            registry.detect(&sample)
        }
    };

    let record = match registry.parse_line(line, format, 0) {
        Ok(r) => r,
        Err(_) => return Ok(None),
    };

    let dict = PyDict::new(py);

    // Timestamp as ISO 8601 string
    dict.set_item(
        "timestamp",
        record.timestamp.map(|ts| ts.to_rfc3339()),
    )?;

    // Level as lowercase string
    dict.set_item("level", record.level.map(|l| l.as_str()))?;

    // Message
    dict.set_item("message", record.message)?;

    // Fields: convert Value<'a> to Python objects
    let fields = PyDict::new(py);
    for (key, value) in &record.fields {
        let py_val: PyObject = match value {
            Value::Str(s) => s.into_py(py),
            Value::Int(i) => i.into_py(py),
            Value::Float(f) => f.into_py(py),
            Value::Bool(b) => b.into_py(py),
            Value::Null => py.None(),
        };
        fields.set_item(*key, py_val)?;
    }
    dict.set_item("fields", fields)?;

    // Format
    dict.set_item("format", record.format.as_str())?;

    // Line number
    dict.set_item("line_number", record.line_number)?;

    Ok(Some(dict.into()))
}

/// Parse multiple log lines and return a list of dicts for successfully parsed lines.
#[pyfunction]
fn parse_lines(
    py: Python<'_>,
    lines: Vec<String>,
    format_hint: Option<&str>,
) -> PyResult<Vec<PyObject>> {
    let registry = ParserRegistry::new();

    let format = match format_hint {
        Some(hint) => hint
            .parse::<Format>()
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?,
        None => {
            let refs: Vec<&str> = lines.iter().take(10).map(String::as_str).collect();
            registry.detect(&refs)
        }
    };

    let mut results = Vec::with_capacity(lines.len());

    for (i, line) in lines.iter().enumerate() {
        let record = match registry.parse_line(line, format, i as u64) {
            Ok(r) => r,
            Err(_) => continue,
        };

        let dict = PyDict::new(py);
        dict.set_item("timestamp", record.timestamp.map(|ts| ts.to_rfc3339()))?;
        dict.set_item("level", record.level.map(|l| l.as_str()))?;
        dict.set_item("message", record.message)?;

        let fields = PyDict::new(py);
        for (key, value) in &record.fields {
            let py_val: PyObject = match value {
                Value::Str(s) => s.into_py(py),
                Value::Int(i) => i.into_py(py),
                Value::Float(f) => f.into_py(py),
                Value::Bool(b) => b.into_py(py),
                Value::Null => py.None(),
            };
            fields.set_item(*key, py_val)?;
        }
        dict.set_item("fields", fields)?;
        dict.set_item("format", record.format.as_str())?;
        dict.set_item("line_number", record.line_number)?;

        results.push(dict.into());
    }

    Ok(results)
}

/// Drain template extraction tree, wrapping logmole-core's DrainTree.
#[pyclass]
struct DrainTree {
    inner: RustDrainTree,
}

#[pymethods]
impl DrainTree {
    /// Create a new DrainTree with the given parameters.
    #[new]
    #[pyo3(signature = (depth=4, similarity_threshold=0.4, max_clusters=1000))]
    fn new(depth: usize, similarity_threshold: f64, max_clusters: usize) -> Self {
        Self {
            inner: RustDrainTree::new(depth, similarity_threshold, max_clusters),
        }
    }

    /// Process a log message and return its template ID.
    fn process(&mut self, message: &str) -> u32 {
        self.inner.process(message)
    }

    /// Get all templates sorted by frequency (descending).
    /// Returns list of (template_id, pattern_string, count).
    fn templates(&self) -> Vec<(u32, String, u64)> {
        self.inner
            .templates_sorted()
            .iter()
            .map(|t| (t.id, t.pattern(), t.count))
            .collect()
    }

    /// Get a single template by ID.
    /// Returns (pattern_string, count) or None.
    fn get_template(&self, id: u32) -> Option<(String, u64)> {
        self.inner.get_template(id).map(|t| (t.pattern(), t.count))
    }

    /// Number of unique templates.
    fn template_count(&self) -> usize {
        self.inner.template_count()
    }

    /// Check if a template ID is novel (not seen during baseline).
    fn is_novel(&self, template_id: u32, baseline_max_id: u32) -> bool {
        self.inner.is_novel(template_id, baseline_max_id)
    }

    /// Get the highest template ID currently assigned.
    fn max_id(&self) -> u32 {
        self.inner.max_id()
    }
}

/// Python module definition.
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(detect_format, m)?)?;
    m.add_function(wrap_pyfunction!(parse_line, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lines, m)?)?;
    m.add_class::<DrainTree>()?;
    Ok(())
}
