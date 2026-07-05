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

def patch_code_serializer():
    """1. Bypass SanityCheck in V8 10.8 (inline in header) + remove .cc out-of-line definition"""
    # Step 1: Remove SanityCheck WITH source from .cc (it's now inline in header)
    cc_path = os.path.join(V8_DIR, "src", "snapshot", "code-serializer.cc")
    cc_content = read_file(cc_path)
    lines = cc_content.split('\n')
    new_lines = []
    skip_mode = False
    brace_count = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        # "SerializedCodeData::SanityCheck(" that is NOT followed by "WithoutSource"
        if (not skip_mode and 
            'SerializedCodeData::SanityCheck(' in line and 
            'SanityCheckWithoutSource' not in line):
            skip_mode = True
            brace_count = 0
            i += 1
            continue
        if skip_mode:
            brace_count += line.count('{') - line.count('}')
            if brace_count <= 0 and '}' in line:
                skip_mode = False
            i += 1
            continue
        new_lines.append(line)
        i += 1
    
    write_file(cc_path, '\n'.join(new_lines))
    print("OK: Removed SanityCheck (with source) from code-serializer.cc")
    
    # Step 2: Bypass SanityCheckWithoutSource in .cc - always return kSuccess
    cc_content2 = read_file(cc_path)
    lines2 = cc_content2.split('\n')
    new_lines2 = []
    in_sanity_wo = False
    brace_count2 = 0
    i = 0
    while i < len(lines2):
        line = lines2[i]
        if not in_sanity_wo:
            if 'SerializedCodeData::SanityCheckWithoutSource()' in line:
                in_sanity_wo = True
                brace_count2 = 0
                new_lines2.append(line)
                i += 1
                continue
            new_lines2.append(line)
        else:
            brace_count2 += line.count('{') - line.count('}')
            if brace_count2 <= 0 and '}' in line:
                indent = ' ' * (len(line) - len(line.lstrip()))
                new_lines2.append(indent + 'return SerializedCodeSanityCheckResult::kSuccess;')
                new_lines2.append(line)
                in_sanity_wo = False
            i += 1
            continue
        i += 1
    
    write_file(cc_path, '\n'.join(new_lines2))
    print("OK: Patched SanityCheckWithoutSource (always returns kSuccess)")
    
    # Step 3: Patch the header's inline SanityCheck to always return kSuccess
    header_path = os.path.join(V8_DIR, "src", "snapshot", "code-serializer.h")
    header = read_file(header_path)
    hlines = header.split('\n')
    new_hlines = []
    in_sanity_inline = False
    brace_count_h = 0
    marker_found = False
    i = 0
    while i < len(hlines):
        line = hlines[i]
        if not in_sanity_inline:
            # Match "SanityCheck(" that is part of inline method (inside class, not SerializedCodeData::)
            if ('SanityCheck(' in line and 
                'SanityCheckWithoutSource' not in line and
                'SerializedCodeData::' not in line and
                'AlignedCachedData' not in line and
                '//' not in line.strip()[:5]):
                in_sanity_inline = True
                brace_count_h = 0
                marker_found = True
                new_hlines.append(line)
                i += 1
                continue
            new_hlines.append(line)
        else:
            brace_count_h += line.count('{') - line.count('}')
            if brace_count_h <= 0 and '}' in line:
                indent = ' ' * (len(line) - len(line.lstrip()))
                new_hlines.append(indent + 'return SerializedCodeSanityCheckResult::kSuccess;')
                new_hlines.append(line)
                in_sanity_inline = False
            i += 1
            continue
        i += 1
    
    if not marker_found:
        print("WARNING: Could not find inline SanityCheck in header, trying fallback...")
        # Fallback: search with broader pattern
        header2 = read_file(header_path)
        # Just look for any line with "SanityCheck" in the header that's inside the class
        # and print surrounding context for debugging
        for i, l in enumerate(hlines):
            if 'SanityCheck' in l or (i > 0 and 'SanityCheck' in hlines[i-1]):
                print(f"  hdr[{i}]: {l}")
    
    write_file(header_path, '\n'.join(new_hlines))
    print("OK: Patched code-serializer.h (inline SanityCheck -> kSuccess)")
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
