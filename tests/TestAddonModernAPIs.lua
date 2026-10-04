local root = assert(arg[1], "Pass addon directory")
local bridge = {S={}, C={GuidType={},unitPlayer="player",Spell={AutoShotId=75}}, Libs={RangeCheck={}}}
bridge.SafeUnitGUID=function() return "Player-70-00000001" end
local env = setmetatable({
  GetBuildInfo=function() return "1.60.1","70205","date",16001,"Beta" end,
  C_AddOns={GetAddOnMetadata=function(name,key)
    if name~="MasterOfAgentsBridge" then return nil end
    return key=="Version" and "1.13.2" or "camelot"
  end},
  C_Item={GetItemInfo=function(id) return "Jerky",nil,1,1,1,nil,nil,1,nil,133972,1,nil,nil,2 end,
    GetItemSpell=function(id) return "Hearthstone",8690 end,
    IsUsableItem=function() return true end},
  C_Spell={GetSpellInfo=function(id) return {name="spell",iconID=1,castTime=id==8690 and 10000 or 0,spellID=id} end,
    GetSpellPowerCost=function() return {} end},
  GetSpellBaseCooldown=function() return 30000,1500 end,
  C_Container={GetContainerItemInfo=function() return {iconFileID=133972,stackCount=4,isLocked=false,quality=1,isReadable=false,hasLoot=false,hyperlink="item:117",isFiltered=false,hasNoValue=false,itemID=117,isBound=false} end},
  C_GossipInfo={GetOptions=function() return {} end},C_FriendList={},
  C_Map={GetBestMapForUnit=function() return 1 end,GetPlayerMapPosition=function() end},
  Enum={PowerType={}}, bit={band=function() return 0 end,rshift=function() return 0 end},
  CreateFrame=function() return {} end,GetInventorySlotInfo=function() return 0 end,
  WOW_PROJECT_ID=11,WOW_PROJECT_MAINLINE=1,WOW_PROJECT_CLASSIC=2,
  COMBATLOG_OBJECT_TYPE_PLAYER=0x400,COMBATLOG_OBJECT_TYPE_PET=0x1000,
}, {__index=_G})
env._G=env
local function loadModule(name)
  local modulePath = name==arg[2] and arg[3] or (root.."/"..name)
  local chunk=assert(loadfile(modulePath)); setfenv(chunk,env)
  chunk("MasterOfAgentsBridge",{bridge})
end
loadModule("Versions.lua")
assert(type(bridge.GetItemInfo)=="function", "missing namespaced item-info adapter")
assert(select(14,bridge.GetItemInfo(117))==2, "item bind type lost")
assert(select(2,bridge.GetItemSpell(6948))==8690, "item spell lost")
assert(select(2,bridge.GetSpellBaseCooldown(8690))==1500, "base cooldown lost")
assert(bridge.GetAddonVersion()=="1.13.2", "metadata version lost")
print("PASS modern API adapters")
bridge.SafeGetActionInfo=function() return "item",6948 end
local result
bridge.actionBarCastTimeQueue={set=function(self,slot,value) assert(slot==1); result=value end}
loadModule("Query.lua")
bridge:populateActionbarCastTime(1)
assert(result==60000,"item cast time/flag incorrectly encoded")
print("PASS real Query item action")
loadModule("Collections.lua")
loadModule("MasterOfAgentsBridge.lua")
local welcome
bridge.Print=function(self,message) welcome=message; error("TEST_STOP_AFTER_WELCOME") end
if arg[4]~="bag-only" then
  local ok,err=pcall(bridge.OnEnteringWorld,bridge)
  assert(not ok and tostring(err):find("TEST_STOP_AFTER_WELCOME",1,true), tostring(err))
  assert(welcome=="Welcome. Using 1.13.2", "world-entry version missing")
  print("PASS real world-entry metadata")
end
-- Exercise the actual private bag-flag function captured by the renderer.
local visited={}
local function findUpvalue(fn,wanted)
  if visited[fn] then return end
  visited[fn]=true
  for i=1,100 do
    local name,value=debug.getupvalue(fn,i)
    if not name then break end
    if name==wanted then return value end
    if type(value)=="function" then
      local result=findUpvalue(value,wanted)
      if result then return result end
    end
  end
end
local flags
for _,value in pairs(bridge) do
  if type(value)=="function" then flags=findUpvalue(value,"GetItemFlags") or flags end
end
assert(flags,"bag flag consumer not exercised")
assert(flags(0,1,"item:117")==1,"tradeable bag item flags incorrect")
assert(flags(0,1,nil)==0,"empty bag slot flags incorrect")
print("PASS real renderer bag flags")
loadModule("EventHandlers.lua")
bridge.InitializeErrorLists=function() end
welcome=nil
local loginOK,loginError=pcall(bridge.OnPlayerLogin,bridge)
assert(not loginOK and tostring(loginError):find("TEST_STOP_AFTER_WELCOME",1,true),tostring(loginError))
assert(welcome=="Welcome. Using 1.13.2","login version missing")
print("PASS real player-login metadata")
