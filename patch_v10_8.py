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

def replace_func_body_smart(file_content, search_str, exclude_strs=None):
    """Replace a function body (between opening { and matching }) with 'return kSuccess'.
    
    search_str: substring that appears in the function signature line
    exclude_strs: list of substrings that must NOT appear in the matched line
    """
    lines = file_content.split('\n')
    
    # Step 1: Find the function signature
    sig_idx = -1
    for i, line in enumerate(lines):
        if search_str in line:
            if exclude_strs and any(excl in line for excl in exclude_strs):
                continue
            sig_idx = i
            break
    
    if sig_idx == -1:
        print(f"ERROR: Cannot find signature: '{search_str}'")
        return file_content, False
    
    # Step 2: Find the opening brace {. It could be on the same line or a continuation line.
    brace_line_idx = -1
    for j in range(sig_idx, min(sig_idx + 3, len(lines))):
        if '{' in lines[j]:
            brace_line_idx = j
            break
    
    if brace_line_idx == -1:
        print(f"ERROR: Cannot find opening brace for '{search_str}'")
        return file_content, False
    
    # Step 3: Count braces from the opening brace line to find the matching closing brace
    indent = lines[brace_line_idx][:len(lines[brace_line_idx]) - len(lines[brace_line_idx].lstrip())]
    brace_count = 0
    close_brace_idx = -1
    
    for j in range(brace_line_idx, len(lines)):
        brace_count += lines[j].count('{') - lines[j].count('}')
        if brace_count <= 0:
            close_brace_idx = j
            break
    
    if close_brace_idx == -1:
        print(f"ERROR: Cannot find closing brace for '{search_str}'")
        return file_content, False
    
    # Step 4: Reconstruct the file, replacing body lines with 'return kSuccess'
    new_lines = []
    for i in range(len(lines)):
        if i == brace_line_idx:
            new_lines.append(lines[i])
        elif brace_line_idx < i < close_brace_idx:
            if i == brace_line_idx + 1:
                new_lines.append(indent + '  return SerializedCodeSanityCheckResult::kSuccess;')
        elif i == close_brace_idx:
            new_lines.append(lines[i])
        else:
            new_lines.append(lines[i])
    
    return '\n'.join(new_lines), True

def patch_code_serializer():
    """1. Bypass all three SanityCheck methods using smart brace-counting replacement"""
    cc_path = os.path.join(V8_DIR, "src", "snapshot", "code-serializer.cc")
    cc = read_file(cc_path)
    
    # Bypass SanityCheck - must NOT match SanityCheckJustSource or SanityCheckWithoutSource
    cc, ok = replace_func_body_smart(cc, 'SerializedCodeData::SanityCheck(', 
                                     exclude_strs=['SanityCheckJustSource', 'SanityCheckWithoutSource'])
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
        '#include "src/objects/string-inl.h"',
        '#include "src/diagnostics/disassembler.h"',
        '#include <unordered_set>',
        '#include <sstream>',
    ]
    for inc in reversed(new_includes):
        lines.insert(last_src_include + 1, inc)
    
    # --- Step B: Insert Disassemble + LoadJSC after RealmSharedSet ---
    new_functions = [
        '',
        '// ===== jsc2js patch: Disassemble and LoadJSC =====',
        'static void DisassembleBytecode(v8::internal::Isolate* isolate,',
        '                                v8::internal::Handle<v8::internal::SharedFunctionInfo> sfi,',
        '                                std::unordered_set<uintptr_t>& visited,',
        '                                int depth) {',
        '  if (depth > 100) { return; }',
        '  v8::internal::Handle<v8::internal::BytecodeArray> bc((*sfi).GetBytecodeArray(isolate), isolate);',
        '  uintptr_t key = reinterpret_cast<uintptr_t>(bc->GetFirstBytecodeAddress());',
        '  if (visited.count(key)) { return; }',
        '  visited.insert(key);',
        '',
        '  // Print indentation',
        '  for (int i = 0; i < depth; ++i) v8::internal::PrintF("  ");',
        '  // Print function name',
        '  auto name = (*sfi).Name();',
        '  if (name.length() > 0) {',
        '    v8::internal::PrintF("Function: %s\\n", name.ToCString().get());',
        '  } else {',
        '    v8::internal::PrintF("Function: (anonymous)\\n");',
        '  }',
        '  // Print bytecode',
        '  // Print bytecode',
        '  v8::internal::OFStream os(stdout);',
        '  bc->Disassemble(os);',
        '  v8::internal::PrintF("\\n");',
        '',
        '  // Dump constants && recurse into inner functions',
        '  v8::internal::FixedArray consts = bc->constant_pool();',
        '  for (int i = 0; i < consts.length(); i++) {',
        '    v8::internal::Object obj = consts.get(i);',
        '    if (obj.IsString()) {',
        '      v8::internal::String str = v8::internal::String::cast(obj);',
        '      v8::internal::PrintF("  [C%d]: \\"%s\\"\\n", i, str.ToCString().get());',
        '    }',
        '    if (obj.IsSharedFunctionInfo()) {',
        '      v8::internal::SharedFunctionInfo inner_sfi = v8::internal::SharedFunctionInfo::cast(obj);',
        '      if (inner_sfi.HasBytecodeArray()) {',
        '        auto inner_handle = v8::internal::handle(inner_sfi, isolate);',
        '        DisassembleBytecode(isolate, inner_handle, visited, depth + 1);',
        '      }',
        '    }',
        '  }',
        '}',
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
        '    DisassembleBytecode(isolate, fun, visited, 0);',
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

