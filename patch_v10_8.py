#!/usr/bin/env python3
"""Apply jsc2js patches to V8 10.8.168.25 source directly."""

import os
import sys

V8_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v8") if "v8" not in os.getcwd() else "."

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def patch_code_serializer():
    """1. Bypass SanityCheck in code-serializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "code-serializer.cc")
    content = read_file(path)

    # Find and replace SanityCheck + SanityCheckWithoutSource using line-based matching
    lines = content.split('\n')
    new_lines = []
    in_sanity = False
    in_sanity_without = False
    brace_count = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if not in_sanity and 'SerializedCodeData::SanityCheck(' in line and 'SanityCheckWithoutSource()' not in line:
            in_sanity = True
            brace_count = 0
            new_lines.append(line)  # Keep function signature
            i += 1
            while i < len(lines) and in_sanity:
                l = lines[i]
                brace_count += l.count('{') - l.count('}')
                if brace_count <= 0 and '}' in l:
                    # End of function
                    indent = ' ' * (len(l) - len(l.lstrip()))
                    new_lines.append(indent + 'return SerializedCodeSanityCheckResult::kSuccess;')
                    new_lines.append(l)  # Closing brace
                    in_sanity = False
                i += 1
        elif not in_sanity_without and 'SerializedCodeData::SanityCheckWithoutSource()' in line:
            in_sanity_without = True
            brace_count = 0
            new_lines.append(line)  # Keep function signature
            i += 1
            while i < len(lines) and in_sanity_without:
                l = lines[i]
                brace_count += l.count('{') - l.count('}')
                if brace_count <= 0 and '}' in l:
                    indent = ' ' * (len(l) - len(l.lstrip()))
                    new_lines.append(indent + 'return SerializedCodeSanityCheckResult::kSuccess;')
                    new_lines.append(l)
                    in_sanity_without = False
                i += 1
        else:
            new_lines.append(line)
            i += 1
    
    write_file(path, '\n'.join(new_lines))
    print("OK: Patched code-serializer.cc (body replacement)")
    return True

def patch_deserializer():
    """2. Comment magic_number check in deserializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "deserializer.cc")
    content = read_file(path)

    old_check = "  CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"
    new_check = "  //CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"

    if old_check not in content:
        print("ERROR: magic_number check pattern not found in deserializer.cc")
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
        print("ERROR: Rehash() pattern not found in object-deserializer.cc")
        return False
    content = content.replace(old_rehash, new_rehash)

    write_file(path, content)
    print("OK: Patched object-deserializer.cc")
    return True

def patch_d8_cc():
    """4. Add LoadJSC + Disassemble to d8.cc (V8 10.8 APIs)"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.cc")
    content = read_file(path)

    # Use exact content from V8 10.8.168.25:
    # Line 2160: void Shell::RealmSharedSet(Local<String> property, Local<Value> value,
    # Line 2161:                            const PropertyCallbackInfo<void>& info) {
    # Line 2162:   Isolate* isolate = info.GetIsolate();
    # Line 2163:   PerIsolateData* data = PerIsolateData::Get(isolate);
    # Line 2164:   data->realm_shared_.Reset(isolate, value);
    # Line 2165: }
    # Line 2166: (blank)
    # Line 2167: // Realm.takeWebSnapshot(index, exports) takes a snapshot ...
    
    # Use line-based approach to find and replace
    lines = content.split('\n')
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'void Shell::RealmSharedSet(' in line:
            # Found RealmSharedSet. Collect the function block.
            new_lines.append(line)  # line 1: function signature
            i += 1
            new_lines.append(lines[i])  # line 2: const PropertyCallbackInfo<void>& info) {
            i += 1
            # Skip function body until closing brace
            brace_count = 1
            while i < len(lines) and brace_count > 0:
                l = lines[i]
                brace_count += l.count('{') - l.count('}')
                i += 1
            # i is now after the closing brace line
            
            # Insert new code
            new_lines.append('')
            new_lines.append('#include "src/snapshot/code-serializer.h"')
            new_lines.append('#include "src/objects/objects-inl.h"')
            new_lines.append('#include <iostream>')
            new_lines.append('#include <unordered_set>')
            new_lines.append('')
            new_lines.append('static void DisassembleBytecode(v8::internal::Isolate* isolate,')
            new_lines.append('                                v8::internal::Tagged<v8::internal::BytecodeArray> bytecode,')
            new_lines.append('                                std::unordered_set<uintptr_t>& visited,')
            new_lines.append('                                int depth) {')
            new_lines.append('  if (depth > 100) { return; }')
            new_lines.append('  uintptr_t key = reinterpret_cast<uintptr_t>(bytecode.ptr());')
            new_lines.append('  if (visited.count(key)) { return; }')
            new_lines.append('  visited.insert(key);')
            new_lines.append('  auto consts = bytecode.constant_pool();')
            new_lines.append('  for (int i = 0; i < consts.length(); i++) {')
            new_lines.append('    auto obj = consts.get(i);')
            new_lines.append('    if (v8::internal::IsSharedFunctionInfo(obj)) {')
            new_lines.append('      auto shared = v8::internal::Cast<v8::internal::SharedFunctionInfo>(obj);')
            new_lines.append('      if (shared.HasBytecodeArray()) {')
            new_lines.append('        DisassembleBytecode(isolate, shared.GetBytecodeArray(isolate), visited, depth + 1);')
            new_lines.append('      }')
            new_lines.append('    }')
            new_lines.append('  }')
            new_lines.append('}')
            new_lines.append('')
            new_lines.append('void v8::Shell::LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args) {')
            new_lines.append('  auto isolate = reinterpret_cast<v8::internal::Isolate*>(args.GetIsolate());')
            new_lines.append('  for (int i = 0; i < args.Length(); i++) {')
            new_lines.append('    v8::String::Utf8Value filename(args.GetIsolate(), args[i]);')
            new_lines.append('    if (*filename == NULL) {')
            new_lines.append('      args.GetIsolate()->ThrowException(v8::Exception::Error(')
            new_lines.append('          v8::String::NewFromUtf8(args.GetIsolate(), "Error loading file").ToLocalChecked()));')
            new_lines.append('      return;')
            new_lines.append('    }')
            new_lines.append('    int length = 0;')
            new_lines.append('    auto filedata = reinterpret_cast<uint8_t*>(ReadChars(*filename, &length));')
            new_lines.append('    if (filedata == NULL) {')
            new_lines.append('      args.GetIsolate()->ThrowException(v8::Exception::Error(')
            new_lines.append('          v8::String::NewFromUtf8(args.GetIsolate(), "Error reading file").ToLocalChecked()));')
            new_lines.append('      return;')
            new_lines.append('    }')
            new_lines.append('    v8::internal::AlignedCachedData cached_data(filedata, length);')
            new_lines.append('    auto source = isolate->factory()')
            new_lines.append('                      ->NewStringFromUtf8(base::CStrVector("source"))')
            new_lines.append('                      .ToHandleChecked();')
            new_lines.append('    v8::ScriptOriginOptions origin_options;')
            new_lines.append('    v8::internal::MaybeHandle<v8::internal::SharedFunctionInfo> maybe_fun =')
            new_lines.append('        v8::internal::CodeSerializer::Deserialize(isolate, &cached_data, source, origin_options);')
            new_lines.append('')
            new_lines.append('    v8::internal::Handle<v8::internal::SharedFunctionInfo> fun;')
            new_lines.append('    if (!maybe_fun.ToHandle(&fun)) {')
            new_lines.append('      args.GetIsolate()->ThrowException(v8::Exception::Error(')
            new_lines.append('          v8::String::NewFromUtf8(args.GetIsolate(), "Deserialize failed, possibly version mismatch or invalid .jsc file").ToLocalChecked()));')
            new_lines.append('      delete[] filedata;')
            new_lines.append('      return;')
            new_lines.append('    }')
            new_lines.append('')
            new_lines.append('    v8::internal::PrintF("---- Starting disassembly of %s ----\\n", *filename);')
            new_lines.append('    fflush(stdout);')
            new_lines.append('')
            new_lines.append('    std::unordered_set<uintptr_t> visited;')
            new_lines.append('    DisassembleBytecode(isolate, fun->GetBytecodeArray(isolate), visited, 0);')
            new_lines.append('')
            new_lines.append('    v8::internal::PrintF("---- Finished disassembly of %s ----\\n", *filename);')
            new_lines.append('    fflush(stdout);')
            new_lines.append('')
            new_lines.append('    delete[] filedata;')
            new_lines.append('  }')
            new_lines.append('}')
            new_lines.append('')
            
            # Continue with the next line (should be the blank or comment)
        else:
            new_lines.append(line)
            i += 1
    
    content = '\n'.join(new_lines)
    
    # Now add loadjsc to CreateGlobalTemplate
    old_load = """  global_template->Set(isolate, "load",
                       FunctionTemplate::New(isolate, ExecuteFile));
  global_template->Set(isolate, "setTimeout","""
    new_load = """  global_template->Set(isolate, "load",
                       FunctionTemplate::New(isolate, ExecuteFile));
  global_template->Set(
      v8::String::NewFromUtf8(isolate, "loadjsc", v8::NewStringType::kNormal)
          .ToLocalChecked(),
      v8::FunctionTemplate::New(isolate, v8::Shell::LoadJSC));
  global_template->Set(isolate, "setTimeout","""

    if old_load not in content:
        print("ERROR: CreateGlobalTemplate load pattern not found")
        print("Looking for alternative patterns...")
        # Try alternative - line-based for CreateGlobalTemplate
        lines2 = content.split('\n')
        for idx, l in enumerate(lines2):
            if 'ExecuteFile' in l and 'load' in l:
                print(f"  Found at line {idx}: {l.strip()}")
        return False
    content = content.replace(old_load, new_load)

    write_file(path, content)
    print("OK: Patched d8.cc")
    return True

def patch_d8_h():
    """5. Add LoadJSC declaration to d8.h"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.h")
    content = read_file(path)

    old_decl = """  static void ReportException(Isolate* isolate, TryCatch* try_catch);
  static MaybeLocal<String> ReadFile(Isolate* isolate, const char* name,"""
    new_decl = """  static void ReportException(Isolate* isolate, TryCatch* try_catch);
  static void LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args);
  static MaybeLocal<String> ReadFile(Isolate* isolate, const char* name,"""

    if old_decl not in content:
        print("ERROR: ReportException pattern not found in d8.h")
        return False
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
