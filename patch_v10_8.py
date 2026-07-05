#!/usr/bin/env python3
"""Apply jsc2js patches to V8 10.8.168.25 source directly."""

import os, sys

V8_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v8") if "v8" not in os.getcwd() else "."

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def replace_func_body_smart(file_content, func_sig_line, expected_body_start_line=None):
    """Replace a function body (between opening { and matching }) with 'return kSuccess'.
    
    Returns (modified_content, True/False).
    func_sig_line: The line containing the function signature (e.g. 'SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(')
    """
    lines = file_content.split('\n')
    
    # Step 1: Find the function signature
    sig_idx = -1
    for i, line in enumerate(lines):
        if func_sig_line in line:
            sig_idx = i
            break
    
    if sig_idx == -1:
        print(f"ERROR: Cannot find signature: '{func_sig_line}'")
        return file_content, False
    
    # Step 2: Find the opening brace {. It could be on the same line or a continuation line.
    brace_line_idx = -1
    for j in range(sig_idx, min(sig_idx + 3, len(lines))):
        if '{' in lines[j]:
            brace_line_idx = j
            break
    
    if brace_line_idx == -1:
        print(f"ERROR: Cannot find opening brace for '{func_sig_line}'")
        return file_content, False
    
    # Step 3: Count braces from the opening brace line to find the matching closing brace
    # We start counting from the position of the first { in the opening brace line
    indent = lines[brace_line_idx][:len(lines[brace_line_idx]) - len(lines[brace_line_idx].lstrip())]
    brace_count = 0
    close_brace_idx = -1
    
    for j in range(brace_line_idx, len(lines)):
        brace_count += lines[j].count('{') - lines[j].count('}')
        if brace_count <= 0:
            close_brace_idx = j
            break
    
    if close_brace_idx == -1:
        print(f"ERROR: Cannot find closing brace for '{func_sig_line}'")
        return file_content, False
    
    # Step 4: Reconstruct the file, replacing body lines with 'return kSuccess'
    new_lines = []
    for i in range(len(lines)):
        if i == brace_line_idx:
            # Opening brace line - keep as-is
            new_lines.append(lines[i])
        elif brace_line_idx < i < close_brace_idx:
            # Body lines - skip
            if i == brace_line_idx + 1:
                # Add the return statement after the opening brace
                new_lines.append(indent + '  return SerializedCodeSanityCheckResult::kSuccess;')
        elif i == close_brace_idx:
            # Closing brace - keep as-is
            new_lines.append(lines[i])
        else:
            # Lines outside the function - keep as-is
            new_lines.append(lines[i])
    
    return '\n'.join(new_lines), True

def patch_code_serializer():
    """1. Bypass all three SanityCheck methods using smart brace-counting replacement"""
    cc_path = os.path.join(V8_DIR, "src", "snapshot", "code-serializer.cc")
    cc = read_file(cc_path)
    
    # Bypass SanityCheck (the one that calls SanityCheckWithoutSource + SanityCheckJustSource)
    cc, ok = replace_func_body_smart(cc, 'SerializedCodeData::SanityCheck(uint32_t')
    if not ok: return False
    print("OK: SanityCheck -> kSuccess")
    
    # Bypass SanityCheckJustSource
    cc, ok = replace_func_body_smart(cc, 'SerializedCodeData::SanityCheckJustSource(')
    if not ok: return False
    print("OK: SanityCheckJustSource -> kSuccess")
    
    # Bypass SanityCheckWithoutSource
    cc, ok = replace_func_body_smart(cc, 'SerializedCodeData::SanityCheckWithoutSource()')
    if not ok: return False
    print("OK: SanityCheckWithoutSource -> kSuccess")
    
    write_file(cc_path, cc)
    return True

def patch_deserializer():
    """2. Comment magic_number check in deserializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "deserializer.cc")
    content = read_file(path)
    old_check = "  CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"
    new_check = "  //CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"
    if old_check not in content:
        print("ERROR: magic_number check not found in deserializer.cc")
        return False
    content = content.replace(old_check, new_check)
    write_file(path, content)
    print("OK: Patched deserializer.cc")
    return True

def patch_object_deserializer():
    """3. Comment Rehash() in object-deserializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "object-deserializer.cc")
    content = read_file(path)
    old_rehash = "  Rehash();"
    new_rehash = "  // Rehash();"
    if old_rehash not in content:
        print("ERROR: Rehash() not found in object-deserializer.cc")
        return False
    content = content.replace(old_rehash, new_rehash)
    write_file(path, content)
    print("OK: Patched object-deserializer.cc")
    return True

def patch_d8_cc():
    """4. Add LoadJSC + Disassemble to d8.cc"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.cc")
    content = read_file(path)
    lines = content.split('\n')

    # --- Step A: Add includes after the last src/ include ---
    # Find last line that includes a "src/" header (internal V8 headers)
    last_src_include = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('#include "src/'):
            last_src_include = i
    
    if last_src_include < 0:
        # Fallback: find any #include
        for i, line in enumerate(lines):
            if line.strip().startswith('#include'):
                last_src_include = i
    
    new_includes = [
        '#include "src/snapshot/code-serializer.h"',
        '#include "src/objects/objects-inl.h"',
        '#include <unordered_set>',
    ]
    for inc in reversed(new_includes):
        lines.insert(last_src_include + 1, inc)
    
    # --- Step B: Insert Disassemble + LoadJSC after RealmSharedSet ---
    new_functions = [
        '',
        '// ===== jsc2js patch: Disassemble and LoadJSC =====',
        'static void DisassembleBytecode(v8::internal::Isolate* isolate,',
        '                                v8::internal::Handle<v8::internal::BytecodeArray> bytecode,',
        '                                std::unordered_set<uintptr_t>& visited,',
        '                                int depth) {',
        '  if (depth > 100) { return; }',
        '  uintptr_t key = reinterpret_cast<uintptr_t>(bytecode->GetFirstBytecodeAddress());',
        '  if (visited.count(key)) { return; }',
        '  visited.insert(key);',
        '  v8::internal::FixedArray consts = bytecode->constant_pool();',
        '  for (int i = 0; i < consts.length(); i++) {',
        '    v8::internal::Object obj = consts.get(i);',
        '    if (obj.IsSharedFunctionInfo()) {',
        '      v8::internal::SharedFunctionInfo shared = v8::internal::SharedFunctionInfo::cast(obj);',
        '      if (shared.HasBytecodeArray()) {',
        '      auto shared_handle = v8::internal::handle(shared, isolate);',
        '      v8::internal::Handle<v8::internal::BytecodeArray> inner_bc((*shared_handle).GetBytecodeArray(isolate), isolate);',
        '      DisassembleBytecode(isolate, inner_bc, visited, depth + 1);',
        '      }',
        '    }',
        '  }',
        '}',
        '',
        'void v8::Shell::LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args) {',
        '  auto isolate = reinterpret_cast<v8::internal::Isolate*>(args.GetIsolate());',
        '  for (int i = 0; i < args.Length(); i++) {',
        '    v8::String::Utf8Value filename(args.GetIsolate(), args[i]);',
        '    if (*filename == NULL) {',
        '      args.GetIsolate()->ThrowException(v8::Exception::Error(',
        '          v8::String::NewFromUtf8(args.GetIsolate(), "Error loading file").ToLocalChecked()));',
        '      return;',
        '    }',
        '    int length = 0;',
        '    auto filedata = reinterpret_cast<uint8_t*>(ReadChars(*filename, &length));',
        '    if (filedata == NULL) {',
        '      args.GetIsolate()->ThrowException(v8::Exception::Error(',
        '          v8::String::NewFromUtf8(args.GetIsolate(), "Error reading file").ToLocalChecked()));',
        '      return;',
        '    }',
        '    v8::internal::AlignedCachedData cached_data(filedata, length);',
        '    auto source = isolate->factory()',
        '                      ->NewStringFromUtf8(base::CStrVector("source"))',
        '                      .ToHandleChecked();',
        '    v8::ScriptOriginOptions origin_options;',
        '    v8::internal::MaybeHandle<v8::internal::SharedFunctionInfo> maybe_fun =',
        '        v8::internal::CodeSerializer::Deserialize(isolate, &cached_data, source, origin_options);',
        '',
        '    v8::internal::Handle<v8::internal::SharedFunctionInfo> fun;',
        '    if (!maybe_fun.ToHandle(&fun)) {',
        '      args.GetIsolate()->ThrowException(v8::Exception::Error(',
        '          v8::String::NewFromUtf8(args.GetIsolate(), "Deserialize failed, possibly version mismatch or invalid .jsc file").ToLocalChecked()));',
        '      delete[] filedata;',
        '      return;',
        '    }',
        '',
        '    v8::internal::PrintF("---- Starting disassembly of %s ----\\n", *filename);',
        '    fflush(stdout);',
        '',
        '    std::unordered_set<uintptr_t> visited;',
        '    v8::internal::Handle<v8::internal::BytecodeArray> bc((*fun).GetBytecodeArray(isolate), isolate);',
        '    DisassembleBytecode(isolate, bc, visited, 0);',
        '',
        '    v8::internal::PrintF("---- Finished disassembly of %s ----\\n", *filename);',
        '    fflush(stdout);',
        '',
        '    delete[] filedata;',
        '  }',
        '}',
        '',
    ]
    
    # Find the line after RealmSharedSet closing brace
    insert_idx = -1
    for i, line in enumerate(lines):
        if 'void Shell::RealmSharedSet(' in line:
            j = i + 1
            brace_count = 1
            while j < len(lines) and brace_count > 0:
                brace_count += lines[j].count('{') - lines[j].count('}')
                j += 1
            insert_idx = j
            while insert_idx < len(lines) and lines[insert_idx].strip() == '':
                insert_idx += 1
            break
    
    if insert_idx < 0:
        print("ERROR: RealmSharedSet not found in d8.cc")
        return False
    
    for nf in reversed(new_functions):
        lines.insert(insert_idx, nf)
    
    # --- Step C: Add loadjsc to CreateGlobalTemplate ---
    content = '\n'.join(lines)
    old_load = '  global_template->Set(isolate, "load",\n                       FunctionTemplate::New(isolate, ExecuteFile));'
    new_load = '''  global_template->Set(isolate, "load",
                       FunctionTemplate::New(isolate, ExecuteFile));
  global_template->Set(
      v8::String::NewFromUtf8(isolate, "loadjsc", v8::NewStringType::kNormal)
          .ToLocalChecked(),
      v8::FunctionTemplate::New(isolate, v8::Shell::LoadJSC));'''
    
    if old_load not in content:
        print("ERROR: CreateGlobalTemplate load pattern not found")
        return False
    content = content.replace(old_load, new_load)
    
    write_file(path, content)
    print("OK: Patched d8.cc")
    return True

def patch_d8_h():
    """5. Add LoadJSC declaration to d8.h"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.h")
    content = read_file(path)

    old_decl = "  static void ReportException(Isolate* isolate, TryCatch* try_catch);"
    if old_decl not in content:
        print("ERROR: ReportException not found in d8.h")
        return False
    
    new_decl = "  static void ReportException(Isolate* isolate, TryCatch* try_catch);\n  static void LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args);"
    content = content.replace(old_decl, new_decl)

    write_file(path, content)
    print("OK: Patched d8.h")
    return True

def main():
    os.chdir(V8_DIR)
    results = [
        patch_code_serializer(),
        patch_deserializer(),
        patch_object_deserializer(),
        patch_d8_cc(),
        patch_d8_h(),
    ]
    if all(results):
        print("\nAll patches applied successfully!")
        return 0
    else:
        print("\nSome patches FAILED!")
        return 1

if __name__ == "__main__":
    sys.exit(main())
